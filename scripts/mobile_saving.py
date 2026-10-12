#!/usr/bin/env python3
"""
scripts/mobile_saving.py — "IP-only" saving mode: borrow the phone's carrier IP
for ONE signup, without routing all traffic (browsers/downloads) over mobile.

Why: tethering makes the phone the default route, so every byte — including the
~190 MB Chromium downloads — is billed to your mobile quota. That's how a 30 GB
plan empties. This mode keeps heavy traffic on Wi-Fi and sends only the farm's
signup requests through the phone.

Two supported wirings:
  1. phone-proxy : a proxy app on the phone exposes its mobile connection at
                   <phone-ip>:<port>. Only the signup browser uses it.
  2. never-set   : nothing is routed through the phone; you just rotate the
                   carrier IP and use the phone directly ONLY for the signup.

Plus a DATA BUDGET guard: it reads the phone's mobile usage over ADB and refuses
to exceed --cap-mb per run, so a runaway can't drain the plan again.

Usage:
  python scripts/mobile_saving.py status
  python scripts/mobile_saving.py start --cap-mb 50            # begin a budgeted session
  python scripts/mobile_saving.py used                          # mobile MB since start
  python scripts/mobile_saving.py stop
  python scripts/mobile_saving.py run --cap-mb 20 -- python job.py   # kill job past 20 MB

Laptop side: NM profile "phone-tether" (enp0s20f0u2) keeps its default route in
table 900, so only traffic bound to that interface (curl --interface, a proxy
bound to it) uses mobile data; everything else stays on Wi-Fi.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
STATE = Path.home() / ".config" / "auto-freecf" / "mobile_saving.json"


def _adb(*args: str, serial: str | None = None, timeout: int = 20) -> str:
    try:
        cmd = ["adb"] + (["-s", serial] if serial else []) + list(args)
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def _serial() -> str | None:
    """First USB phone; network adb targets (Waydroid ip:port) are skipped."""
    out = _adb("devices")
    for line in out.splitlines()[1:]:
        if line.strip().endswith("device") and ":" not in line.split()[0]:
            return line.split()[0]
    return None


# rmnet_data*/ccmni* carry the billed traffic; rmnet_ipa0 is the hardware link
# underneath them, so counting it too would double every byte.
MOBILE_PREFIXES = ("rmnet_data", "ccmni", "wwan")


def parse_net_dev(text: str) -> dict[str, tuple[int, int]]:
    """/proc/net/dev -> {iface: (rx, tx)} for the mobile interfaces."""
    out = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        name, f = name.strip(), rest.split()
        if name.startswith(MOBILE_PREFIXES) and len(f) >= 9:
            out[name] = (int(f[0]), int(f[8]))
    return out


def _mobile_iface(serial: str) -> str | None:
    """Comma-joined mobile interfaces, if any."""
    names = parse_net_dev(_adb("shell", "cat", "/proc/net/dev", serial=serial))
    return ",".join(sorted(names)) or None


def _mobile_rx_tx(serial: str) -> tuple[int, int]:
    """Bytes rx/tx summed over all mobile interfaces (cumulative since boot)."""
    v = parse_net_dev(_adb("shell", "cat", "/proc/net/dev", serial=serial)).values()
    return sum(r for r, _ in v), sum(t for _, t in v)


def status() -> int:
    s = _serial()
    if not s:
        print("  ✗ no Android device via adb (connect the phone first)"); return 3
    rx, tx = _mobile_rx_tx(s)
    iface = _mobile_iface(s)
    print(f"  device      : {s}")
    print(f"  mobile iface: {iface or '(none — phone may be on Wi-Fi only)'}")
    print(f"  mobile usage: {(rx+tx)/1e6:.1f} MB total (rx {rx/1e6:.1f} / tx {tx/1e6:.1f})")
    print("  saving note : keep Wi-Fi as the default route; only the signup uses the phone.")
    return 0


def start(cap_mb: float) -> int:
    s = _serial()
    if not s:
        print("  ✗ no Android device"); return 3
    rx, tx = _mobile_rx_tx(s)
    st = {"serial": s, "start_ts": time.time(), "cap_mb": cap_mb, "start_bytes": rx + tx}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st))
    print(f"  ✓ saving session started | cap {cap_mb} MB | baseline {(rx+tx)/1e6:.1f} MB")
    return 0


def used() -> int:
    if not STATE.exists():
        print("  ✗ no session (run: start --cap-mb N)"); return 1
    st = json.loads(STATE.read_text())
    rx, tx = _mobile_rx_tx(st["serial"])
    mb = (rx + tx - st["start_bytes"]) / 1e6
    cap = st["cap_mb"]
    pct = (mb / cap * 100) if cap else 0
    over = mb >= cap
    print(f"  used {mb:.1f} MB of {cap} MB ({pct:.0f}%){'  ⛔ OVER CAP — stop' if over else ''}")
    return 5 if over else 0  # exit 5 = over budget (callers can stop the farm)


def run(cap_mb: float, cmd: list[str], poll: float = 3.0) -> int:
    """Run cmd; kill it once phone mobile data used since start passes cap_mb."""
    s = _serial()
    if not s:
        print("  ✗ no Android device"); return 3
    base = sum(_mobile_rx_tx(s))
    p = subprocess.Popen(cmd)
    mb = 0.0
    while p.poll() is None:
        time.sleep(poll)
        mb = (sum(_mobile_rx_tx(s)) - base) / 1e6
        if mb >= cap_mb:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
            print(f"  ⛔ mobile data {mb:.1f} MB ≥ cap {cap_mb} MB — job killed")
            return 5
    mb = (sum(_mobile_rx_tx(s)) - base) / 1e6
    print(f"  mobile data used: {mb:.1f} MB (cap {cap_mb} MB)")
    return p.returncode


def stop() -> int:
    STATE.unlink(missing_ok=True)
    print("  stopped")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Phone IP-only saving mode + data budget")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    s = sub.add_parser("start"); s.add_argument("--cap-mb", type=float, default=50)
    sub.add_parser("used")
    sub.add_parser("stop")
    r = sub.add_parser("run", help="run a job, kill it past the cap: run --cap-mb 20 -- cmd ...")
    r.add_argument("--cap-mb", type=float, default=20); r.add_argument("job", nargs=argparse.REMAINDER)
    a = ap.parse_args(argv)
    if a.cmd == "run":
        job = a.job[1:] if a.job[:1] == ["--"] else a.job
        return run(a.cap_mb, job) if job else ap.error("run needs a command")
    return {"status": status, "start": lambda: start(a.cap_mb), "used": used, "stop": stop}[a.cmd]()


if __name__ == "__main__":
    sys.exit(main())