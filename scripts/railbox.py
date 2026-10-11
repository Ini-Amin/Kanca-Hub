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

Facts (railway.com/free-vm): identity = SSH key; box needs a pty ONCE to be created,
then plain ssh/tar pipes work; 60 min build window; max 3 boxes per IP per day.
ponytail: unclaimed boxes live 60 min, so "forever" = rotation, capped at 3/day/IP
(run it from different egress, e.g. `vpngate.py up`); claiming (account) is the real fix.
"""
import argparse, fcntl, json, os, pty, re, select, shutil, struct, subprocess, sys, termios, time
from pathlib import Path

ROOT = Path.home() / ".config" / "auto-freecf" / "railbox"
STATE = ROOT / "boxes.json"
SNAP = Path.home() / ".local" / "share" / "auto-freecf" / "railbox-snapshot"
REPO = Path(__file__).resolve().parent.parent
EXCLUDE = [".git/objects", "rea", "Sider_CAPTCHA_Solver", "PloxCardGen", "node_modules", "__pycache__", "*.pyc"]
ANSI = re.compile(rb"\x1b\[[0-9;?]*[a-zA-Z]|\x1b[()][0-9A-B]")


def load():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save(d):
    ROOT.mkdir(parents=True, exist_ok=True); STATE.write_text(json.dumps(d, indent=1))


def ssh_base(name):
    return ["ssh", "-i", str(ROOT / name / "key"), "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=no", "-o", "ServerAliveInterval=20", "railway.new"]


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
    r = subprocess.Popen(ssh_base(name) + ["tar czf - -C /app/work ."], stdout=subprocess.PIPE)
    t = subprocess.run(["tar", "xzf", "-", "-C", str(dst)], stdin=r.stdout)
    r.wait(); print("pull rc", r.returncode, t.returncode)
    return r.returncode or t.returncode


def wipe(name):
    # files first (rm), then forget the key; Railway deletes the unclaimed box itself at expiry
    run(name, "rm -rf /app/work /root/.config /root/.ssh /root/.bash_history /tmp/* 2>/dev/null; echo wiped", timeout=60)
    shutil.rmtree(ROOT / name, ignore_errors=True)
    d = load(); d.pop(name, None); save(d); print("wiped", name)


def rotate(margin):
    old = live()
    nn = f"b{int(time.time())}"
    if old:
        if pull(old, SNAP): sys.exit("pull failed; old box kept, nothing erased")
    create(nn)
    if old:
        if push(nn, SNAP): sys.exit("push failed; old box kept")
        wipe(old)
    return nn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["new", "status", "push", "pull", "ssh", "wipe", "rotate", "daemon"])
    ap.add_argument("rest", nargs="*"); ap.add_argument("--name"); ap.add_argument("--margin", type=int, default=10)
    a = ap.parse_args()
    if a.cmd == "new": create(a.name or f"b{int(time.time())}")
    elif a.cmd == "status":
        for n, b in load().items():
            print(n, f"{(b['expires']-time.time())/60:.0f} min left", b["claim"], b["preview"])
    elif a.cmd == "rotate": rotate(a.margin)
    elif a.cmd == "daemon":
        while True:
            n = live()
            if not n or load()[n]["expires"] - time.time() <= a.margin * 60:
                try: rotate(a.margin)
                except SystemExit as e: print("rotate:", e)
            time.sleep(30)
    else:
        n = a.name or live() or sys.exit("no live box")
        if a.cmd == "push": sys.exit(push(n, a.rest[0] if a.rest else REPO))
        if a.cmd == "pull": sys.exit(pull(n, a.rest[0] if a.rest else SNAP))
        if a.cmd == "wipe": wipe(n)
        if a.cmd == "ssh": os.execvp("ssh", ssh_base(n)[:-1] + ["-tt", "railway.new"] + a.rest)


if __name__ == "__main__":
    main()
