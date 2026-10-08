#!/usr/bin/env python3
"""
scripts/egress_probe.py — one-line egress verdict for the current IP (or --proxy).

Reuses scripts/diagnose.py's classify_egress (never imports kancahub).

Usage:
  python scripts/egress_probe.py                 # current egress
  python scripts/egress_probe.py --proxy URL     # probe through a proxy
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from diagnose import classify_egress  # noqa: E402  (pure classifier, no kancahub)

PROBE_URL = "http://ip-api.com/json/?fields=query,isp,hosting,proxy,mobile"

def parse_info(raw: str | bytes) -> dict:
    """Decode the ip-api JSON payload into a dict; raise ValueError if unusable."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    try:
        info = json.loads(raw)
    except Exception as e:
        raise ValueError(f"bad probe response: {e}") from e
    if not isinstance(info, dict):
        raise ValueError("bad probe response: not an object")
    return info

def format_verdict(info: dict) -> str:
    """One-line verdict: '<ip>  <STATUS>  <note>  [isp]'."""
    status, note = classify_egress(info)
    ip = info.get("query") or "?"
    isp = info.get("isp") or "?"
    return f"{ip}  {status}  {note}  [{isp}]"

def fetch_info(proxy: str | None = None, timeout: float = 15.0) -> dict:
    import urllib.request
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        urlopen = opener.open
    else:
        urlopen = urllib.request.urlopen
    with urlopen(PROBE_URL, timeout=timeout) as r:
        return parse_info(r.read())

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="One-line egress verdict (current IP or --proxy).")
    ap.add_argument("--proxy", help="probe through this proxy URL instead of the current egress")
    args = ap.parse_args(argv)
    try:
        print(format_verdict(fetch_info(args.proxy)))
    except Exception as e:
        print(f"?  UNKNOWN  probe failed: {str(e)[:80]}")
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())