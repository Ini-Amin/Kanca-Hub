#!/usr/bin/env python3
"""scripts/r9_conncheck.py -- probe every 9Router providerConnection and report health.

Reads ~/.9router/db/data.sqlite (read-only), hits each connection's endpoint with a
real tiny request, and writes a health snapshot to /tmp/opencode/r9_health.json.

This is the missing input for failover: 9Router's own testStatus goes stale
(it only updates on a real request), so we probe for ground truth.

Usage:
  python scripts/r9_conncheck.py                # probe all
  python scripts/r9_conncheck.py --provider P   # probe one provider prefix
  python scripts/r9_conncheck.py --no-net       # just dump stored state
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

DB = Path(os.path.expanduser("~/.9router/db/data.sqlite"))
OUT = Path("/tmp/opencode/r9_health.json")
TIMEOUT = 25

# a cheap model per provider family to probe with (first that the provider serves)
PROBE_MODEL = {
    "openai-compatible-chat-1d39647b-193d-4f65-b38b-03d80c92460a": "deepseek-v4-flash:free",
    "antigravity": "gemini-3.5-flash-low",
}


def load():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cols = [r[1] for r in c.execute("PRAGMA table_info(providerConnections)")]
    rows = [dict(zip(cols, r)) for r in c.execute("SELECT * FROM providerConnections")]
    for r in rows:
        try:
            r["_d"] = json.loads(r["data"]) if r["data"] else {}
        except Exception:
            r["_d"] = {}
    return rows


def endpoint(row):
    """Return (url, api_key) for a connection, or (None, None) if not probeable."""
    d = row["_d"]
    psd = d.get("providerSpecificData") or {}
    url = psd.get("baseUrl") or d.get("baseUrl")
    if not url:
        prov = row["provider"]
        if prov == "antigravity":
            url = "https://antigravity.google/api/v1"
        else:
            return None, None
    if not url.rstrip("/").endswith("/v1") and "completions" not in url:
        url = url.rstrip("/") + "/v1"
    key = d.get("apiKey") or d.get("accessToken")
    return url.rstrip("/") + "/chat/completions", key


def probe(row):
    prov = row["provider"]
    url, key = endpoint(row)
    base = {
        "id": row["id"],
        "provider": prov,
        "name": row["name"],
        "priority": row["priority"],
        "is_active": row["isActive"],
        "stored_status": (row["_d"] or {}).get("testStatus"),
    }
    if not url or not key:
        base["result"] = "unprobeable"
        base["detail"] = "no baseUrl/apiKey"
        return base
    model = PROBE_MODEL.get(prov, "deepseek-v4.1-flash")
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 5,
    }).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            base["result"] = "ok"
            base["http"] = r.status
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode()).get("error", {})
            detail = detail.get("message") if isinstance(detail, dict) else str(detail)
        except Exception:
            detail = ""
        base["result"] = f"http{e.code}"
        base["http"] = e.code
        base["detail"] = str(detail)[:160]
    except Exception as e:  # noqa: BLE001
        base["result"] = "error"
        base["detail"] = str(e)[:120]
    return base


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", help="only connections whose provider starts with this")
    ap.add_argument("--no-net", action="store_true", help="skip probing; dump stored state")
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    rows = load()
    if args.provider:
        rows = [r for r in rows if r["provider"].startswith(args.provider)]
    print(f"connections: {len(rows)}", file=sys.stderr)

    if args.no_net:
        res = [{
            "id": r["id"], "provider": r["provider"], "name": r["name"],
            "priority": r["priority"], "is_active": r["isActive"],
            "stored_status": (r["_d"] or {}).get("testStatus"),
            "stored_error": str((r["_d"] or {}).get("lastError"))[:120],
            "result": "stored",
        } for r in rows]
    else:
        with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
            res = list(ex.map(probe, rows))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=2))

    # summarize
    by_prov: dict[str, dict[str, int]] = {}
    for r in res:
        b = by_prov.setdefault(r["provider"], {})
        b[r["result"]] = b.get(r["result"], 0) + 1
    print(f"{'provider':54} {'ok':>3} {'total':>5}  breakdown")
    for prov, b in sorted(by_prov.items(), key=lambda kv: -kv[1].get("ok", 0)):
        tot = sum(b.values())
        print(f"{prov[:54]:54} {b.get('ok', 0):>3} {tot:>5}  "
              + ", ".join(f"{k}={v}" for k, v in sorted(b.items()) if k != "ok"))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
