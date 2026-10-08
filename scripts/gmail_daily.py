#!/usr/bin/env python3
"""
scripts/gmail_daily.py — Gmail farm with a DAILY quota (create N/day, then stop).

Creates up to `--daily` Google accounts per day and refuses further runs once the
cap is hit (safe-by-default: won't blow past your limit). State persists in
~/.config/auto-freecf/gmail_daily.json so a cron/timer can run it daily.

Each account: TinyFish/cloud-CDP signup -> Litensi number at the phone gate ->
SMS via the webhook (scripts/sms_webhook.py + tunnel).

Usage:
  python scripts/gmail_daily.py status            # how many today / limit
  python scripts/gmail_daily.py run --daily 2     # create until today's cap
  python scripts/gmail_daily.py reset
Cron (daily 10:00):  0 10 * * *  <venv>/python <repo>/scripts/gmail_daily.py run --daily 2
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

HOME = Path.home()
AUTO = HOME / "Auto-FreeCF"
SCRIPTS = Path(__file__).resolve().parent
STATE = HOME / ".config" / "auto-freecf" / "gmail_daily.json"
OUT = HOME / ".local/share/auto-freecf" / "google_accounts.json"


def _load() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save(d: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d, indent=2))


def _today_state() -> dict:
    d = _load()
    today = date.today().isoformat()
    if d.get("day") != today:
        d = {"day": today, "created": 0, "attempts": 0}
    return d


def status() -> int:
    d = _today_state()
    print(f"  date    : {d['day']}")
    print(f"  created : {d['created']}")
    print(f"  attempts: {d['attempts']}")
    return 0


def run(daily: int, *, ws_country: str = "US") -> int:
    d = _today_state()
    remaining = max(0, daily - int(d.get("created", 0)))
    print(f"  daily cap {daily} | created today {d.get('created',0)} | remaining {remaining}")
    if remaining == 0:
        print("  ✓ daily quota already met — stopping (nothing created)")
        return 0
    made = 0
    for i in range(remaining):
        print(f"\n  ── account {i+1}/{remaining} ──")
        d["attempts"] = int(d.get("attempts", 0)) + 1
        _save(d)
        cmd = [sys.executable, str(SCRIPTS / "google_signup_cdp.py"), "--tinyfish",
               "--country", ws_country, "--sms-webhook"]
        try:
            rc = subprocess.call(cmd, cwd=str(AUTO))
        except KeyboardInterrupt:
            print("  stopped"); break
        # success if the account was saved
        n = 0
        try:
            n = len(json.loads(OUT.read_text()))
        except Exception:
            pass
        if rc == 0:
            made += 1
            d["created"] = int(d.get("created", 0)) + 1
            _save(d)
            print(f"  ✓ created ({d['created']}/{daily} today)")
        else:
            print(f"  ⚠ attempt failed (rc={rc})")
        # pace between accounts
        if i < remaining - 1:
            time.sleep(30)
    print(f"\n  done: created {made} this run ({d.get('created',0)}/{daily} today)")
    return 0 if made else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Gmail farm with a daily quota")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    r = sub.add_parser("run"); r.add_argument("--daily", type=int, default=2); r.add_argument("--country", default="US")
    sub.add_parser("reset")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        return status()
    if a.cmd == "reset":
        _save({}); print("  reset"); return 0
    return run(a.daily, ws_country=a.country)


if __name__ == "__main__":
    sys.exit(main())