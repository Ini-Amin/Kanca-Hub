#!/usr/bin/env python3
"""
scripts/gmail_slow.py — drip-feed Gmail creation, one account every few days.

Creating many Gmails quickly trips Google's abuse detection. This wrapper
enforces a minimum INTERVAL between accounts (default 3.5 days) and creates at
most one per run, so a daily cron/timer produces a slow, human-ish cadence.

It does NOT bypass Google's phone step — it only paces account creation. It
drives whatever backend is available:
  device  : a real Android phone/emulator via ADB (gmail_adb.py) — trusted device,
            usually skips the phone gate
  desktop : scripts/gmail_creator.py (nodriver Chrome) — may ask for a phone

State lives in ~/.config/auto-freecf/gmail_slow.json.

Usage (camoufox venv, browser paths):
  python scripts/gmail_slow.py check                 # next eligible time
  python scripts/gmail_slow.py run                   # create ONE if due
  python scripts/gmail_slow.py run --interval-days 4 --backend desktop
  python scripts/gmail_slow.py reset                 # clear the timer
Cron (daily at 10:00):  0 10 * * *  <venv>/python <repo>/scripts/gmail_slow.py run
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
AUTO_FREECF = HOME / "Auto-FreeCF"
SCRIPTS = Path(__file__).resolve().parent
STATE = HOME / ".config" / "auto-freecf" / "gmail_slow.json"
DEFAULT_INTERVAL_DAYS = 3.5


def _load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save(data: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(data, indent=2))


def _due(interval_days: float) -> tuple[bool, float]:
    """(is_due, seconds_until_due)."""
    last = float(_load().get("last_created_ts") or 0)
    if not last:
        return True, 0.0
    wait = interval_days * 86400 - (time.time() - last)
    return (wait <= 0), max(0.0, wait)


def _human(sec: float) -> str:
    if sec <= 0:
        return "now"
    d, rem = divmod(int(sec), 86400)
    h, m = divmod(rem // 60, 60)
    return f"{d}d {h}h" + (f" {m}m" if d == 0 else "")


def _device_connected() -> bool:
    try:
        out = subprocess.run(["adb", "devices"], capture_output=True, text=True, timeout=15).stdout
        return any(l.strip().endswith("device") for l in out.splitlines()[1:])
    except Exception:
        return False


def _run_backend(backend: str, count: int) -> int:
    if backend == "auto":
        backend = "device" if _device_connected() else "desktop"
    py = sys.executable
    if backend == "device":
        tool = SCRIPTS / "gmail_adb.py"
        cmd = [py, str(tool), "--count", str(count)]
    else:
        tool = SCRIPTS / "gmail_creator.py"
        cmd = [py, str(tool), "--count", str(count)]
    if not tool.exists():
        print(f"  ✗ {tool.name} not found")
        return 1
    print(f"  ▶ backend={backend}  ({' '.join(str(c) for c in cmd)})")
    rc = subprocess.call(cmd, cwd=str(AUTO_FREECF))
    # gmail_creator/gmail_adb exit 0 only when an account completed.
    if rc == 0:
        data = _load()
        data["last_created_ts"] = time.time()
        data["count"] = int(data.get("count", 0)) + count
        data["backend"] = backend
        _save(data)
    else:
        print("  ⚠ no account created this run — timer NOT advanced (will retry next run)")
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Drip-feed Gmail creation (1 per few days)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("check", "run", "reset"):
        s = sub.add_parser(name)
        if name == "run":
            s.add_argument("--interval-days", type=float, default=DEFAULT_INTERVAL_DAYS,
                           help=f"min days between accounts (default {DEFAULT_INTERVAL_DAYS})")
            s.add_argument("--backend", choices=["auto", "device", "desktop"], default="auto")
            s.add_argument("--count", type=int, default=1, help="accounts per due run (default 1)")
    a = ap.parse_args(argv)

    if a.cmd == "reset":
        _save({})
        print("  ✓ timer cleared — next run is due immediately")
        return 0

    if a.cmd == "check":
        due, wait = _due(DEFAULT_INTERVAL_DAYS)
        d = _load()
        print(f"  created so far : {d.get('count', 0)}")
        print(f"  last created   : {time.strftime('%Y-%m-%d %H:%M', time.localtime(d['last_created_ts'])) if d.get('last_created_ts') else 'never'}")
        print(f"  next due       : {'now' if due else 'in ' + _human(wait)}")
        return 0

    due, wait = _due(a.interval_days)
    if not due:
        print(f"  • not due yet — next account in {_human(wait)} (interval {a.interval_days}d)")
        return 0
    print(f"  • due now — creating {a.count} Gmail account(s)")
    return _run_backend(a.backend, a.count)


if __name__ == "__main__":
    sys.exit(main())