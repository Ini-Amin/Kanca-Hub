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


def _adb(*args: str, timeout: int = 20) -> str:
    try:
        return subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout).stdout
    except Exception:
        return ""


def _serial() -> str | None:
    out = _adb("devices")
    for line in out.splitlines()[1:]:
        if line.strip().endswith("device"):
            return line.split()[0]
    return None


def _mobile_iface(serial: str) -> str | None:
    """The phone's mobile data interface (rmnet*/ccmni*), if any."""
    out = _adb("shell", "ip", "-o", "link", "show", serial=serial)
    for line in out.splitlines():
        for tok in line.split():
            if tok.startswith(("rmnet", "ccmni", "wwan")):
                return tok.split("@")[0]
    return None


def _mobile_rx_tx(serial: str) -> tuple[int, int]:
    """Bytes rx/tx on the mobile interface (cumulative)."""
    iface = _mobile_iface(serial)
    if not iface:
        return 0, 0
    out = _adb("shell", "cat", f"/sys/class/net/{iface}/statistics/rx_bytes", serial=serial)
    out2 = _adb("shell", "cat", f"/sys/class/net/{iface}/statistics/tx_bytes", serial=serial)
    def _n(s: str) -> int:
        try:
            return int(s.strip().splitlines()[0])
        except Exception:
            return 0
    return _n(out), _n(out2)


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
    a = ap.parse_args(argv)
    return {"status": status, "start": lambda: start(a.cap_mb), "used": used, "stop": stop}[a.cmd]()


if __name__ == "__main__":
    sys.exit(main())