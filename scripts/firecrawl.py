#!/usr/bin/env python3
"""
scripts/firecrawl.py — use the Firecrawl keys already sitting in 9Router.

9Router has active `firecrawl` provider connections we never used. This pulls a
key from the 9Router DB (or $FIRECRAWL_API_KEY) and exposes scrape + search, so
the CLI can fetch clean markdown / find pages without fighting proxies.

Usage:
  python scripts/firecrawl.py scrape https://example.com
  python scripts/firecrawl.py search "residential proxy free trial no kyc" --limit 5
  python scripts/firecrawl.py key           # which key is in use (masked)
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.request
from pathlib import Path

DB = Path(os.environ.get("NINE_ROUTER_HOME", Path.home() / ".9router")) / "db" / "data.sqlite"
BASE = "https://api.firecrawl.dev/v1"


def _key() -> str:
    k = os.environ.get("FIRECRAWL_API_KEY", "")
    if k:
        return k
    try:
        con = sqlite3.connect(DB)
        row = con.execute(
            "SELECT data FROM providerConnections WHERE provider LIKE '%firecrawl%' "
            "AND isActive=1 LIMIT 1").fetchone()
    except Exception:
        row = None
    if row and row[0]:
        try:
            return json.loads(row[0]).get("apiKey", "")
        except Exception:
            pass
    return ""


def scrape(url: str, *, formats=("markdown",), timeout: int = 60) -> dict:
    k = _key()
    if not k:
        return {"error": "no firecrawl key (9Router DB or $FIRECRAWL_API_KEY)"}
    body = json.dumps({"url": url, "formats": list(formats)}).encode()
    req = urllib.request.Request(f"{BASE}/scrape", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {k}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def search(query: str, *, limit: int = 5, timeout: int = 90) -> dict:
    k = _key()
    if not k:
        return {"error": "no firecrawl key"}
    body = json.dumps({"query": query, "limit": limit}).encode()
    req = urllib.request.Request(f"{BASE}/search", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {k}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Firecrawl (via the keys in 9Router)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scrape"); s.add_argument("url"); s.add_argument("--json", action="store_true")
    q = sub.add_parser("search"); q.add_argument("query"); q.add_argument("--limit", type=int, default=5)
    sub.add_parser("key")
    a = ap.parse_args(argv)

    if a.cmd == "key":
        k = _key()
        print("  key:", (k[:8] + "..." ) if k else "(none)")
        return 0 if k else 1
    if a.cmd == "scrape":
        res = scrape(a.url)
        if a.json:
            print(json.dumps(res)[:4000]); return 0
        md = (res.get("data") or {}).get("markdown") or res.get("error") or json.dumps(res)[:300]
        print(md[:4000])
        return 0
    if a.cmd == "search":
        res = search(a.query, limit=a.limit)
        items = res.get("data") or []
        if isinstance(items, dict):
            items = items.get("web") or []
        for it in items[:a.limit]:
            if isinstance(it, dict):
                print(f"  - {it.get('title','')}\n    {it.get('url','')}\n    {str(it.get('description',''))[:120]}")
        if not items:
            print(json.dumps(res)[:600])
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())