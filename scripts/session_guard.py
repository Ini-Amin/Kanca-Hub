#!/usr/bin/env python3
"""
session_guard.py — CGNAT-aware session/IP stability guard.

Background (why this exists): with mobile CGNAT, many users share one public IP,
so sites are RELUCTANT to ban a mobile IP — but they DO watch for:
  * the SAME account switching IP mid-session (geolocation inconsistency)
  * too many NEW accounts acting identically on one IP at once (CIB)
  * a changing browser fingerprint per account
The winning pattern is: ONE sticky IP per account for the whole session, stable
fingerprint, spaced-out account creation, human-like pacing. This module enforces
the IP half of that.

It records, per account key (email/handle):
  * the exit IP at session START,
  * the exit IP at session END,
  * whether the IP stayed STABLE (no mid-session switch),
  * how many accounts have already used that IP (reuse count).

Usage (CLI):
  python3 scripts/session_guard.py start --account a@x.com     # record start IP
  python3 scripts/session_guard.py end   --account a@x.com     # check stability
  python3 scripts/session_guard.py report                       # per-IP reuse table
  python3 scripts/session_guard.py ips                          # list IPs + counts

As a library:
  from session_guard import guard_start, guard_end, reuse_count
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

STATE_PATH = Path(os.environ.get("SESSION_GUARD_STATE", Path.home() / ".config" / "auto-freecf" / "session_guard.json"))

# Advisory reuse thresholds (from public guidance, not hard limits):
#   light reading  -> many fine
#   moderate       -> 2-3 accounts/IP
#   heavy/automated-> 1 account/IP per session
REUSE_WARN = 3


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def get_ip(timeout: float = 8.0) -> str | None:
    for url in ("https://api.ipify.org", "https://icanhazip.com"):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                ip = r.read().decode().strip()
            if ip:
                return ip
        except Exception:  # noqa: BLE001
            continue
    return None


def _load(path: Path | str | None = None) -> dict:
    p = Path(path) if path else STATE_PATH
    if not p.exists():
        return {"sessions": {}, "history": []}
    try:
        data = json.loads(p.read_text())
        data.setdefault("sessions", {})
        data.setdefault("history", [])
        return data
    except Exception:  # noqa: BLE001
        return {"sessions": {}, "history": []}


def _save(data: dict, path: Path | str | None = None) -> None:
    p = Path(path) if path else STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def reuse_count(ip: str, path: Path | str | None = None) -> int:
    """How many completed sessions already used this IP."""
    data = _load(path)
    return sum(1 for h in data["history"] if h.get("ip") == ip)


def guard_start(account: str, *, ip: str | None = None, path: Path | str | None = None) -> dict:
    """Record the exit IP at the start of an account session."""
    data = _load(path)
    record = {
        "account": account,
        "ip": ip,                      # may be None; filled later
        "started": _now_iso(),
        "reuse": reuse_count(ip, path) if ip else 0,
    }
    data["sessions"][account] = record
    _save(data, path)
    return record


def guard_end(account: str, *, ip: str | None = None, path: Path | str | None = None) -> dict:
    """Check IP stability for an account session; returns a verdict dict.

    verdict: stable | changed | unknown
    """
    data = _load(path)
    sess = data["sessions"].get(account, {})
    start_ip = sess.get("ip")
    end_ip = ip if ip is not None else get_ip()
    if not start_ip or not end_ip:
        verdict = "unknown"
    elif start_ip == end_ip:
        verdict = "stable"
    else:
        verdict = "changed"
    result = {
        "account": account,
        "start_ip": start_ip,
        "end_ip": end_ip,
        "stable": verdict == "stable",
        "verdict": verdict,
        "reuse_before": sess.get("reuse", reuse_count(start_ip, path) if start_ip else 0),
    }
    if end_ip:
        data["history"].append({"account": account, "ip": end_ip, "ts": _now_iso(), "stable": result["stable"]})
    data["sessions"].pop(account, None)
    _save(data, path)
    return result


def per_ip_report(path: Path | str | None = None) -> str:
    data = _load(path)
    hist = data["history"]
    if not hist:
        return "  (no sessions recorded yet)"
    counts = Counter(h["ip"] for h in hist if h.get("ip"))
    changed = sum(1 for h in hist if not h.get("stable"))
    lines = [f"  {'exit IP':<18} {'accounts':>8}  flag"]
    for ip, n in counts.most_common():
        flag = "⚠ high reuse" if n >= REUSE_WARN else ""
        lines.append(f"  {ip:<18} {n:>8}  {flag}")
    lines.append(f"  total sessions: {len(hist)}   ip changes mid-session: {changed}")
    lines.append("  advice: keep 1 sticky IP per account per session; space out NEW accounts;")
    lines.append("          prefer a device node / mobile egress whose IP is stable during a session.")
    return "\n".join(lines)


def _cli() -> int:
    ap = argparse.ArgumentParser(description="CGNAT-aware session/IP stability guard")
    ap.add_argument("--path", default=None)
    ap.add_argument("--ip", default=None, help="override the detected exit IP")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("start", help="record the egress IP at session start")
    s.add_argument("--account", required=True)
    s.add_argument("--ip", default=None, help="override the detected exit IP")
    e = sub.add_parser("end", help="check IP stability at session end")
    e.add_argument("--account", required=True)
    e.add_argument("--ip", default=None, help="override the detected exit IP")
    sub.add_parser("report", help="per-IP reuse table")
    sub.add_parser("ips", help="just the recorded IPs")

    args = ap.parse_args()
    ip_override = getattr(args, "ip", None)
    if args.cmd == "start":
        ip = ip_override or get_ip()
        r = guard_start(args.account, ip=ip, path=args.path)
        print(f"  ✓ session start {args.account}  ip={ip}  (reuse so far: {r['reuse']})")
        return 0
    if args.cmd == "end":
        r = guard_end(args.account, ip=ip_override, path=args.path)
        mark = {"stable": "✅", "changed": "❌", "unknown": "➖"}[r["verdict"]]
        print(f"  {mark} {args.account}  {r['start_ip']} -> {r['end_ip']}  [{r['verdict']}]")
        if r["verdict"] == "changed":
            print("     ⚠ IP changed mid-session — sites may flag this as inconsistent.")
        return 0
    if args.cmd == "ips":
        data = _load(args.path)
        for ip, n in Counter(h["ip"] for h in data["history"] if h.get("ip")).most_common():
            print(f"  {ip}  x{n}")
        return 0
    print(per_ip_report(args.path))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
