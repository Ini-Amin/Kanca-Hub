#!/usr/bin/env python3
"""
scripts/egress_check.py — pre-flight the exit IP's TRUST before a signup attempt.

DataDome scores IP reputation (type: residential / mobile / datacenter, plus
proxy/VPN flags) as one of its strongest signals. This checks the current
egress (or a proxy you pass) against free IP-intel APIs so we know WHY a
GitHub/signup attempt is likely to pass or fail before burning attempts.

Usage:
  python scripts/egress_check.py                      # current connection
  python scripts/egress_check.py --proxy http://u:p@h:port
  python scripts/egress_check.py --github             # also probe github.com/signup
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

IPAPI_FIELDS = "status,message,query,country,regionName,city,isp,org,as,asname,mobile,proxy,hosting"


def _get(url: str, proxy: str | None = None, timeout: int = 20) -> dict:
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def classify(info: dict) -> tuple[str, str]:
    """Return (level, reason). level in high/medium/low/flagged."""
    if info.get("proxy"):
        return "flagged", "IP flagged as proxy/VPN by the intel DB"
    if info.get("hosting"):
        return "low", "datacenter/hosting IP (DataDome heavily discounts these)"
    if info.get("mobile"):
        return "high", "mobile carrier IP (highest trust)"
    # residential-ish ISP
    return "medium", "residential/ISP IP (good; needs a clean, consistent browser)"


def run(proxy: str | None, check_github: bool) -> int:
    from pathlib import Path
    print(f"  egress: {'proxy ' + proxy if proxy else 'direct connection'}")
    try:
        ipdata = _get(f"http://ip-api.com/json/?fields={IPAPI_FIELDS}", proxy)
    except Exception as e:
        print(f"  ✗ intel lookup failed: {e}")
        return 1
    if ipdata.get("status") != "success":
        print(f"  ✗ intel lookup: {ipdata.get('message')}")
        return 1

    level, reason = classify(ipdata)
    print(f"\n  IP        : {ipdata.get('query')}")
    print(f"  Location  : {ipdata.get('city')}, {ipdata.get('regionName')}, {ipdata.get('country')}")
    print(f"  ISP/Org   : {ipdata.get('isp')} / {ipdata.get('org')}")
    print(f"  ASN       : {ipdata.get('as')}")
    print(f"  Flags     : mobile={bool(ipdata.get('mobile'))} proxy={bool(ipdata.get('proxy'))} hosting={bool(ipdata.get('hosting'))}")
    tag = {"high": "🟢", "medium": "🟡", "low": "🔴", "flagged": "⛔"}[level]
    print(f"  VERDICT   : {tag} {level.upper()} — {reason}")

    if level in ("medium", "high"):
        print("  → worth trying GitHub signup (use a real browser + sticky session).")
    else:
        print("  → expect a DataDome block; use a mobile/residential egress first.")

    if check_github:
        try:
            req = urllib.request.Request("https://github.com/signup", headers={"User-Agent": "Mozilla/5.0"})
            handlers = []
            if proxy:
                handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
            op = urllib.request.build_opener(*handlers)
            with op.open(req, timeout=25) as r:
                code = r.status
        except urllib.error.HTTPError as he:
            code = he.code
        except Exception as e:
            code = f"err:{e}"
        print(f"  github.com/signup -> HTTP {code} {'(403 = DataDome blocking this IP)' if code == 403 else ''}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check exit-IP trust (DataDome pre-flight)")
    ap.add_argument("--proxy", default=None, help="check a proxy's exit instead of the direct connection")
    ap.add_argument("--github", action="store_true", help="also probe github.com/signup")
    a = ap.parse_args(argv)
    return run(a.proxy, a.github)


if __name__ == "__main__":
    sys.exit(main())