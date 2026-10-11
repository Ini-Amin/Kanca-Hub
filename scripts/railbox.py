#!/usr/bin/env python3
"""railbox: rolling free Linux VM from `ssh railway.new` (no account, no card).

  railbox.py new [--name N]     create a box (fresh ed25519 key, state saved)
  railbox.py status             boxes, minutes left, claim URL
  railbox.py push [dir]         tar a local dir -> box:/app/work
  railbox.py pull [dir]         box:/app/work -> local dir
  railbox.py ssh [cmd...]       shell/command on the live box
  railbox.py wipe [--name N]    erase box files + forget its key
  railbox.py rotate [--margin 10]  pull -> new box -> push -> wipe old
  railbox.py daemon [--margin 10]  rotate forever, ~margin min before expiry
  (create runs through a VPN Gate relay, transfers run direct; --no-relay to skip)

Facts (railway.com/free-vm): identity = SSH key; box needs a pty ONCE to be created,
then plain ssh/tar pipes work; 60 min build window; max 3 boxes per IP per day.
ponytail: unclaimed boxes live 60 min, so "forever" = rotation, capped at 3/day/IP
(run it from different egress, e.g. `vpngate.py up`); claiming (account) is the real fix.
"""
import argparse, fcntl, json, os, pty, re, select, shutil, socket, struct, subprocess, sys, termios, time
from pathlib import Path

ROOT = Path.home() / ".config" / "auto-freecf" / "railbox"
STATE = ROOT / "boxes.json"
SNAP = Path.home() / ".local" / "share" / "auto-freecf" / "railbox-snapshot"
REPO = Path(__file__).resolve().parent.parent
EXCLUDE = ["*accounts*.json", "*.sqlite", ".env", "*.key", "secrets", ".git/objects", "rea", "Sider_CAPTCHA_Solver", "PloxCardGen", "node_modules", "__pycache__", "*.pyc"]
ANSI = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][0-9A-B]")


def load():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save(d):
    ROOT.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp"); tmp.write_text(json.dumps(d, indent=1))
    os.replace(tmp, STATE)  # atomic: a power cut mid-write never leaves a half file


def ssh_base(name):
    return ["ssh", "-T", "-i", str(ROOT / name / "key"), "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=no", "-o", "ServerAliveInterval=20", "-o", "LogLevel=ERROR", "railway.new"]


def create(name):
    kd = ROOT / name; kd.mkdir(parents=True, exist_ok=True)
    if not (kd / "key").exists():
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(kd / "key"), "-C", f"railbox-{name}"], check=True)
    pid, fd = pty.fork()
    if pid == 0:
        os.environ["TERM"] = "xterm-256color"
        fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))  # before exec: 0x0 pty => "headless" refusal
        os.execvp("ssh", ssh_base(name)[:-1] + ["-tt", "railway.new"])
    out, t = b"", time.time()
    while time.time() - t < 30 and b"Run `agent`" not in out and b"refused" not in out:
        if select.select([fd], [], [], 1)[0]:
            try: d = os.read(fd, 8192)
            except OSError: break
            if not d: break
            out += d
    try: os.close(fd); os.kill(pid, 15)
    except OSError: pass
    txt = ANSI.sub(b"", out).decode("utf8", "ignore")
    claim = re.search(r"Claim:\s+(https://railway\.com/ssh-signup\?code=\S+)", txt)
    if not claim:
        sys.exit("no box created:\n" + txt[-600:])
    mins = re.search(r"You have (\d+) min", txt)
    prev = re.search(r"https://preview-\S+", txt)
    d = load()
    d[name] = {"claim": claim.group(1), "preview": prev.group(0) if prev else None,
               "expires": time.time() + 60 * int(mins.group(1) if mins else 60)}
    save(d); print(f"box {name}: {mins.group(1) if mins else '?'} min, claim {claim.group(1)}")
    return name


def run(name, cmd, **kw):
    return subprocess.run(ssh_base(name) + [cmd], **kw)


def live():
    d = load()
    ok = {n: b for n, b in d.items() if b["expires"] > time.time()}
    return max(ok, key=lambda n: ok[n]["expires"]) if ok else None


def push(name, src):
    src = Path(src)
    ex = [f"--exclude={e}" for e in EXCLUDE]
    t = subprocess.Popen(["tar", "czf", "-", *ex, "-C", str(src), "."], stdout=subprocess.PIPE)
    r = run(name, "mkdir -p /app/work && tar xzf - -C /app/work", stdin=t.stdout)
    t.wait(); print("push rc", r.returncode)
    return r.returncode


def pull(name, dst):
    dst = Path(dst); dst.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tgz")  # to a file first: a piped tar choked on the ssh stream
    try:
        with open(tmp, "wb") as f:
            r = subprocess.run(ssh_base(name) + ["tar czf - -C /app/work ."], stdout=f, stdin=subprocess.DEVNULL, timeout=300)
        if r.returncode:
            print("pull ssh rc", r.returncode); return r.returncode
        t = subprocess.run(["tar", "xzf", str(tmp), "-C", str(dst)])
    except subprocess.TimeoutExpired:
        print("pull timeout"); return 1
    finally:
        tmp.unlink(missing_ok=True)
    print("pull rc", t.returncode)
    return t.returncode


def wipe(name):
    # files first (rm), then forget the key; Railway deletes the unclaimed box itself at expiry
    run(name, "rm -rf /app/work /root/.config /root/.ssh /root/.bash_history /tmp/* 2>/dev/null; echo wiped", timeout=60)
    shutil.rmtree(ROOT / name, ignore_errors=True)
    d = load(); d.pop(name, None); save(d); print("wiped", name)


def create_via_relay(name, tries=4):
    """Create only: a VPN Gate relay is up for the ~5 s create, then dropped, so file
    transfers go direct and the relay (volunteer-run, logged) never sees our data.
    Plain ssh reaches the box from any IP (verified), so only creation is IP-capped."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import vpngate
    for i in range(tries):
        try:
            if vpngate.up(None, i * 5) != 0:
                continue
            try:
                return create(name)
            except SystemExit as e:
                print("create:", str(e).strip().splitlines()[-1:] or e)
            finally:
                vpngate.down()
        except Exception as e:  # noqa: BLE001
            print("relay:", e)
    sys.exit("no relay produced a box")


def snapshot():
    """Pull the live box into SNAP (atomic: tmp dir then swap) so expiry or a crash never loses work."""
    n = live()
    if not n:
        return 1
    tmp = SNAP.with_name(SNAP.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    if pull(n, tmp):
        shutil.rmtree(tmp, ignore_errors=True)
        return 1
    shutil.rmtree(SNAP, ignore_errors=True); tmp.rename(SNAP)
    return 0


def rotate(margin, relay=True):
    old = live()
    nn = f"b{int(time.time())}"
    if old and snapshot():  # box already expiring/unreachable: carry on with the last good snapshot
        print("snapshot failed; using last good snapshot", flush=True)
    create_via_relay(nn) if relay else create(nn)
    if SNAP.exists() and push(nn, SNAP):
        sys.exit("push failed; old box kept")
    if old:
        wipe(old)
    if SNAP.exists():  # fresh box has no deps: install them (venv lives outside /app/work, so never in SNAP)
        try:
            r = run(nn, "test -f /app/work/scripts/railbox_bootstrap.sh && sh /app/work/scripts/railbox_bootstrap.sh", timeout=900)
            print("bootstrap rc", r.returncode, flush=True)
        except subprocess.TimeoutExpired:
            print("bootstrap timeout", flush=True)
    d = load()
    for k in [k for k, b in d.items() if b["expires"] < time.time() and k != nn]:  # forget expired boxes + keys
        shutil.rmtree(ROOT / k, ignore_errors=True); d.pop(k)
    save(d)
    return nn


def online(host="railway.new", port=22):
    try:
        socket.create_connection((host, port), 5).close(); return True
    except OSError:
        return False


def battery_low(pct=15, root=Path("/sys/class/power_supply")):
    """True only when discharging at/below pct (a power cut): a desktop without BAT* never counts."""
    for b in root.glob("BAT*"):
        try:
            if (b / "status").read_text().strip() == "Discharging" and int((b / "capacity").read_text()) <= pct:
                return True
        except (OSError, ValueError):
            pass
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["new", "status", "push", "pull", "ssh", "wipe", "rotate", "daemon"])
    ap.add_argument("rest", nargs="*"); ap.add_argument("--name"); ap.add_argument("--margin", type=int, default=10); ap.add_argument("--no-relay", action="store_true"); ap.add_argument("--min-battery", type=int, default=15)
    a = ap.parse_args()
    if a.cmd == "new": create(a.name or f"b{int(time.time())}")
    elif a.cmd == "status":
        for n, b in load().items():
            print(n, f"{(b['expires']-time.time())/60:.0f} min left", b["claim"], b["preview"])
    elif a.cmd == "rotate": rotate(a.margin, not a.no_relay)
    elif a.cmd == "daemon":
        last, saved_low, down = 0, False, False
        while True:
            try:
                if not online():  # wifi/router down: don't burn relay tries or the 3/day cap
                    if not down: print("offline; waiting", flush=True)
                    down = True; time.sleep(30); continue
                if down: print("back online", flush=True); down = False
                if battery_low(a.min_battery):  # power cut: save work once, stop creating boxes
                    if not saved_low:
                        print("battery low; final snapshot, rotation paused", flush=True)
                        snapshot(); saved_low = True
                    time.sleep(60); continue
                saved_low = False
                n = live()
                if not n or load()[n]["expires"] - time.time() <= a.margin * 60:
                    rotate(a.margin, not a.no_relay)
                elif time.time() - last > 600:
                    snapshot(); last = time.time()
            except (Exception, SystemExit) as e:  # noqa: BLE001 - daemon must survive any failure
                print("daemon:", e, flush=True); time.sleep(60)
            time.sleep(30)
    else:
        n = a.name or live() or sys.exit("no live box")
        if a.cmd == "push": sys.exit(push(n, a.rest[0] if a.rest else REPO))
        if a.cmd == "pull": sys.exit(pull(n, a.rest[0] if a.rest else SNAP))
        if a.cmd == "wipe": wipe(n)
        if a.cmd == "ssh": os.execvp("ssh", ssh_base(n)[:-1] + ["-tt", "railway.new"] + a.rest)


if __name__ == "__main__":
    main()
