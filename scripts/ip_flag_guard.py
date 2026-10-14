#!/usr/bin/env python3
"""
ip_flag_guard.py — per-egress-IP cooldown after bot-protection flags.

Background: bot-protection vendors (DataDome on github.com, confirmed live
2026-10-14) flag an egress IP for HOURS after repeated challenge hits — even a
real browser then loops the challenge. A farm that keeps probing a flagged IP
only deepens the flag (and burned a whole night's window doing exactly that).

This module records, per exit IP + target host:
  * when the last "challenge/403/429" style block was seen,
  * how many blocks in a row,
  * a cooldown deadline before which the farm must NOT use that IP/target pair.

API:
  flag(ip, host, kind="challenge")     # record a block; extends cooldown
  blocked(ip, host) -> bool            # True while cooling down
  remaining(ip, host) -> float         # seconds left
  clear(ip=None, host=None)            # forget entries (manual override)
  table() -> str                       # human-readable state

CLI: python3 scripts/ip_flag_guard.py [report|flag --ip IP --host H|clear]

Egress policy wiring: auto_egress() consults blocked() per candidate so a
flagged IP is never selected again for that target.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

STATE_PATH = Path(os.environ.get("IP_FLAG_STATE", Path.home() / ".config" / "auto-freecf" / "ip_flag_guard.json"))

# Cooldown policy per flag kind (seconds). DataDome challenges observed to clear
# in ~2-6h; conservative default 3h, extended exponentially per repeat flag.
BASE_COOLDOWN = {
    "challenge": 3 * 3600,   # DataDome "enable JS" loop
    "403": 3600,
    "429": 600,
    "captcha": 3 * 3600,
}
MAX_COOLDOWN = 24 * 3600

def _now() -> float:
    return time.time()

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def _load(path: Path | str | None = None) -> dict:
    p = Path(path) if path else STATE_PATH
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"flags": {}}

def _save(data: dict, path: Path | str | None = None) -> None:
    p = Path(path) if path else STATE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1), encoding="utf-8")

def _key(ip: str, host: str) -> str:
    return f"{ip or '*'}|{host or '*'}"

def flag(ip: str, host: str, kind: str = "challenge", *, path: Path | str | None = None) -> dict:
    """Record a block; cooldown doubles per consecutive flag up to MAX."""
    d = _load(path)
    flags = d.setdefault("flags", {})
    k = _key(ip, host)
    e = flags.get(k) or {"count": 0, "first_ts": _now_iso()}
    e["count"] = int(e.get("count", 0)) + 1
    e["kind"] = kind
    e["last_ts"] = _now_iso()
    base = BASE_COOLDOWN.get(kind, 3600)
    cd = min(base * (2 ** (e["count"] - 1)), MAX_COOLDOWN)
    e["cooldown_until"] = _now() + cd
    e["cooldown_s"] = int(cd)
    flags[k] = e
    d["flags"] = flags
    _save(d, path)
    return e

def entry(ip: str, host: str, *, path: Path | str | None = None) -> dict | None:
    return _load(path).get("flags", {}).get(_key(ip, host))

def remaining(ip: str, host: str, *, path: Path | str | None = None) -> float:
    e = entry(ip, host, path=path)
    if not e:
        return 0.0
    return max(0.0, float(e.get("cooldown_until", 0)) - _now())

def blocked(ip: str, host: str, *, path: Path | str | None = None) -> bool:
    return remaining(ip, host, path=path) > 0

def clear(ip: str | None = None, host: str | None = None, *, path: Path | str | None = None) -> int:
    """Drop matching entries (both None = wipe all). Returns removed count."""
    d = _load(path)
    flags = d.get("flags", {})
    if ip is None and host is None:
        n = len(flags)
        d["flags"] = {}
    else:
        drop = [k for k in flags
                if (ip is None or k.split("|", 1)[0] == ip)
                and (host is None or k.split("|", 1)[1] == host)]
        for k in drop:
            del flags[k]
        n = len(drop)
    _save(d, path)
    return n

def verdict(ip: str | None, host: str | None = "", *, path: Path | str | None = None) -> str:
    """Convenience for egress choosers: 'ok' | 'cooldown:<secs>'."""
    if not ip:
        return "ok"
    r = remaining(ip, host or "*", path=path)
    return "ok" if r <= 0 else f"cooldown:{int(r)}s"

def table(path: Path | str | None = None) -> str:
    d = _load(path)
    flags = d.get("flags", {})
    if not flags:
        return "no flagged IPs"
    rows = []
    for k, e in sorted(flags.items()):
        ip, host = k.split("|", 1)
        r = max(0, float(e.get("cooldown_until", 0)) - _now())
        state = f"cooling {int(r)//3600}h{(int(r)%3600)//60:02d}m" if r > 0 else "expired"
        rows.append(f"{ip:18} {host:28} {e.get('kind','?'):10} x{e.get('count',1):<3} {state}")
    return "\n".join(rows)

def _cli() -> int:
    ap = argparse.ArgumentParser(description="per-IP bot-flag cooldown guard")
    ap.add_argument("cmd", nargs="?", default="report", choices=["report", "flag", "clear"])
    ap.add_argument("--ip", default=None)
    ap.add_argument("--host", default=None)
    ap.add_argument("--kind", default="challenge")
    a = ap.parse_args()
    if a.cmd == "flag":
        if not (a.ip and a.host):
            print("flag needs --ip and --host"); return 1
        e = flag(a.ip, a.host, a.kind)
        print(f"flagged {a.ip}|{a.host} cooldown {e['cooldown_s']//60}m")
    elif a.cmd == "clear":
        print(f"cleared {clear(a.ip, a.host)} entries")
    else:
        print(table())
    return 0

if __name__ == "__main__":
    sys.exit(_cli())
