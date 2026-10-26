#!/usr/bin/env python3
"""proton_servers.py — ProtonVPN free-tier server inventory, no account needed.

The installed flatpak client caches the full server list at
~/.var/app/com.protonvpn.www/cache/Proton/VPN/serverlist.json, so the free-tier
inventory is available offline. Measured 2026-10-23 on this box:

    1617 logical servers total
     121 of them Tier 0 (free)  -> 121 distinct exit IPs

That cache is what the client itself trusts, so it is the honest source for
"what can a free account reach" — no login, no scraping, no guessing.

Usage:
    proton_servers.py list [--cc JP] [--json]
    proton_servers.py refresh          # re-fetch from the API (needs no auth for this route)
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

CACHE = Path.home() / ".var/app/com.protonvpn.www/cache/Proton/VPN/serverlist.json"
# Public server-list route the client uses before login.
API = "https://api.protonvpn.ch/vpn/v1/logicals?SecureCoreFilter=all&WithState=true"
APPVER = "linux-vpn@4.18.2"


def load(path: Path = CACHE) -> list[dict]:
    d = json.loads(path.read_text())
    return d.get("LogicalServers") or []


def free_logicals(path: Path = CACHE) -> list[dict]:
    """Tier 0 only — the servers a free account may connect to."""
    return [x for x in load(path) if x.get("Tier") == 0]


def summarize(logicals: list[dict]) -> list[dict]:
    out = []
    for x in logicals:
        servers = x.get("Servers") or []
        exits = [s.get("ExitIP") for s in servers if s.get("ExitIP")]
        entry = [s.get("EntryIP") for s in servers if s.get("EntryIP")]
        out.append({
            "name": x.get("Name"),
            "country": x.get("ExitCountry"),
            "city": x.get("City"),
            "load": x.get("Load"),
            "status": x.get("Status"),
            "servers": len(servers),
            "exit_ips": exits,
            "entry_ips": entry,
        })
    return out


def refresh(path: Path = CACHE) -> int:
    req = urllib.request.Request(API, headers={"x-pm-appversion": APPVER,
                                              "x-pm-uid": "00000000-0000-0000-0000-000000000000",
                                              "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return len(data.get("LogicalServers") or [])


def main() -> int:
    ap = argparse.ArgumentParser(description="ProtonVPN free-tier server inventory")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list")
    s.add_argument("--cc", help="filter by country code (e.g. JP, NL, US)")
    s.add_argument("--json", action="store_true")
    sub.add_parser("refresh")
    a = ap.parse_args()

    if a.cmd == "refresh":
        try:
            n = refresh()
            print(f"refreshed: {n} logical servers -> {CACHE}")
            return 0
        except Exception as exc:
            print(f"refresh failed ({type(exc).__name__}: {exc}); keeping cache", file=sys.stderr)
            return 1

    rows = summarize(free_logicals())
    if a.cc:
        rows = [r for r in rows if (r["country"] or "").upper() == a.cc.upper()]
    if a.json:
        print(json.dumps(rows, indent=1))
        return 0

    print(f"free (Tier 0) logicals: {len(rows)}")
    by_cc: dict[str, int] = {}
    for r in rows:
        by_cc[r["country"] or "?"] = by_cc.get(r["country"] or "?", 0) + 1
    print("by country:", ", ".join(f"{k}:{v}" for k, v in sorted(by_cc.items(), key=lambda t: -t[1])))
    total_exits = sum(len(r["exit_ips"]) for r in rows)
    print(f"distinct exit IPs: {total_exits}")
    for r in rows[:12]:
        print(f"  {r['name']:<22} {r['country']:<3} {r['city'] or '':<14} "
              f"load={r['load']:<4} exit={r['exit_ips'][0] if r['exit_ips'] else '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
