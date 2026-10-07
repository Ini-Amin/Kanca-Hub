#!/usr/bin/env python3
"""thk_pace_probe.py - find TokenHarbor's safe pace + per-IP burst on the CURRENT egress.

Every attempt is a REAL signup (real account + key). Staircase: start at --start gap, shrink
x0.7 per success until the first throttle ("take a breath"), then measure the cooldown
(retry every 15s), then confirm 4 attempts at 1.3x the throttled gap. Stops at once on any
non-throttle failure (e.g. "IP or email provider is not supported") or --max attempts.
Does NOT rotate the IP: the per-IP limit is what we measure.

  python3 scripts/thk_pace_probe.py [--start 60] [--max 14]
  python3 scripts/thk_pace_probe.py --selfcheck
Log: ~/.config/auto-freecf/thk_pace_probe.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PY = Path.home() / ".local/share/auto-freecf/camoufox-venv/bin/python"  # harbor needs camoufox
LOG = Path.home() / ".config/auto-freecf/thk_pace_probe.jsonl"


def classify(out: str) -> str:
    o = out.lower()
    if "1/1 accounts created" in o:
        return "ok"
    if "too many sign-ups from this network" in o:
        return "netcap"  # per-network cap (~1h): rotate IP, waiting does not help
    if "a bit fast" in o or "take a breath" in o:
        return "throttled"
    if "not supported" in o:
        return "blocked"
    return "error"


def ip() -> str:
    try:
        return urllib.request.urlopen("https://api.ipify.org", timeout=8).read().decode()
    except Exception:  # noqa: BLE001
        return "?"


def attempt(n: int, gap: float, last_end: float | None) -> str:
    env = {**os.environ, "TOKENHARBOR_NO_PROXY": "1"}
    start = time.time()
    p = subprocess.run([str(PY), "-m", "tools.tokenharbor.cli", "batch", "1"], cwd=REPO / "harbor",
                       env=env, capture_output=True, text=True, timeout=240)
    end = time.time()
    text = p.stdout + p.stderr
    status = classify(text)
    import re
    tail = re.sub(r"\x1b\[[0-9;]*m", "", text)
    tail = " | ".join(l.strip() for l in tail.splitlines() if re.search(r"✗|error|fail|Traceback|Error|fast|supported", l, re.I))[-300:]
    rec = {"n": n, "gap_set": round(gap, 1), "since_prev_end": round(start - last_end, 1) if last_end else None,
           "took": round(end - start, 1), "status": status, "why": tail if status != "ok" else "", "ip": ip(), "t": time.strftime("%H:%M:%S")}
    LOG.parent.mkdir(parents=True, exist_ok=True)
    LOG.open("a").write(json.dumps(rec) + "\n")
    print(f"  #{n:<2} gap={gap:>5.1f}s prev_end->start={rec['since_prev_end']}s took={rec['took']}s "
          f"-> {status.upper():9} ip={rec['ip']}", flush=True)
    if status not in ("ok", "throttled") and tail:
        print(f"      why: {tail}", flush=True)
    attempt.end = end
    return status


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=float, default=60)
    ap.add_argument("--max", type=int, default=14)
    ap.add_argument("--selfcheck", action="store_true")
    a = ap.parse_args()
    if a.selfcheck:
        assert classify("✓ 1/1 accounts created") == "ok"
        assert classify("You're doing that a bit fast â take a breath") == "throttled"
        assert classify("Your IP or email provider is not supported") == "blocked"
        assert classify("Too many sign-ups from this network. Please try again in an hour.") == "netcap"
        assert classify("Traceback ...") == "error"
        print("selfcheck OK")
        return 0

    n, gap, last_ok_gap, threshold, retried = 0, a.start, None, None, False
    attempt.end = None
    print(f"egress {ip()}  start gap {gap}s  max {a.max}\n", flush=True)
    while n < a.max:  # phase 1: shrink the gap until the first throttle
        n += 1
        st = attempt(n, gap, attempt.end)
        if st == "ok":
            last_ok_gap, gap = gap, max(5.0, gap * 0.7)
            time.sleep(gap)
            continue
        if st == "throttled":
            threshold = gap
            break
        if st == "netcap":
            print(f"\nNETWORK CAP after {n-1} successful signup(s) on this IP (~1h lockout). "
                  f"Last ok gap: {last_ok_gap}s. Stopped: more attempts only extend it.")
            return 0
        if st == "error" and not retried:
            retried = True
            print("      (one generic error: retrying this step once before giving up)", flush=True)
            time.sleep(30)
            continue
        print(f"\nSTOP: {st} - not a throttle, not probing further.")
        return 1
    if threshold is None:
        print(f"\nNo throttle in {n} attempts; smallest gap that worked: {last_ok_gap}s (try a lower --start floor).")
        return 0

    t0 = time.time()  # phase 2: cooldown
    while n < a.max:
        time.sleep(15)
        n += 1
        st = attempt(n, 15, attempt.end)
        if st == "ok":
            break
        if st == "netcap":
            print(f"\nNETWORK CAP during cooldown after {n-1} attempt(s). Stopped.")
            return 0
        if st != "throttled":
            print(f"\nSTOP: {st} during cooldown.")
            return 1
    cooldown = round(time.time() - t0)

    safe = round(threshold * 1.3)  # phase 3: confirm
    print(f"\nthrottled at gap {threshold:.0f}s; cooldown ~{cooldown}s; confirming at {safe}s x4", flush=True)
    bad = 0
    for _ in range(4):
        if n >= a.max:
            break
        time.sleep(safe)
        n += 1
        st = attempt(n, safe, attempt.end)
        bad += st != "ok"
        if st == "netcap":
            print("\nNETWORK CAP during confirm: pace is not the limiter, the per-IP count is.")
            break
    print(f"\nRESULT: last ok gap {last_ok_gap}s | throttled at {threshold:.0f}s | cooldown ~{cooldown}s | "
          f"confirm at {safe}s: {'clean' if not bad else f'{bad} throttled'} | attempts {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
