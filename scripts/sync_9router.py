#!/usr/bin/env python3
"""
9Router connection health sync for KancaHub.

Fixes the common "N of M connections usable" problem: stale or deactivated
Cloudflare tokens linger in 9Router's DB and show as
`401 Invalid API token or Account ID`.

This tool:
  1. reads every `cloudflare-ai` connection from 9Router's data.sqlite
  2. live-tests each token against Cloudflare's OpenAI-compatible endpoint
  3. reports a health table
  4. with --prune, removes (or deactivates) the dead ones

Usage:
    python3 sync_9router.py                 # report only
    python3 sync_9router.py --prune         # delete dead connections
    python3 sync_9router.py --prune --deactivate   # keep rows, set isActive=0
    python3 sync_9router.py --prune --export-clean clean_keys.txt
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

NINE_ROUTER_HOME = Path(os.environ.get("NINE_ROUTER_HOME", Path.home() / ".9router"))
DB_PATH = NINE_ROUTER_HOME / "db" / "data.sqlite"
CF_PROVIDER = "cloudflare-ai"
PROBE_MODEL = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
CF_URL = "https://api.cloudflare.com/client/v4/accounts/{acct}/ai/v1/chat/completions"


def probe(account_id: str, token: str, timeout: int = 25) -> tuple[bool, str]:
    body = json.dumps({
        "model": PROBE_MODEL,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }).encode()
    req = urllib.request.Request(
        CF_URL.format(acct=account_id), data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read().decode())
            msg = (j.get("errors") or [{}])[0].get("message", "")
            return False, f"HTTP {e.code} {msg}"
        except Exception:
            return False, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return False, type(e).__name__


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify + prune 9Router cloudflare-ai connections")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--prune", action="store_true", help="remove dead connections")
    ap.add_argument("--deactivate", action="store_true",
                    help="with --prune: set isActive=0 instead of deleting")
    ap.add_argument("--export-clean", default=None, help="write working keys to a file")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"✗ 9Router DB not found: {db}", file=sys.stderr)
        return 1

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    rows = list(con.execute(
        "SELECT id, name, data FROM providerConnections WHERE provider = ?", (CF_PROVIDER,)
    ))
    if not rows:
        print("No cloudflare-ai connections found.")
        return 0

    print(f"Testing {len(rows)} cloudflare-ai connection(s)…\n")
    dead_ids, live = [], []
    for r in rows:
        try:
            dd = json.loads(r["data"])
            acct = dd["providerSpecificData"]["accountId"]
            tok = dd["apiKey"]
        except Exception:
            dead_ids.append(r["id"]); print(f"  ⚠️  {r['name']:34s} malformed"); continue
        ok, detail = probe(acct, tok)
        mark = "✅" if ok else "❌"
        print(f"  {mark} {r['name']:34s} {acct[:8]}…  {detail}")
        if ok:
            live.append((r["name"], acct, tok))
        else:
            dead_ids.append(r["id"])

    print(f"\n  {len(live)} working · {len(dead_ids)} dead")

    if args.export_clean and live:
        Path(args.export_clean).write_text(
            "\n".join(f"{n}\t{a}\t{t}" for n, a, t in live) + "\n")
        print(f"  ✓ working keys -> {args.export_clean}")

    if args.prune and dead_ids:
        if not args.deactivate:
            bak = db.with_suffix(f".sqlite.sync-{time.strftime('%Y%m%d-%H%M%S')}.bak")
            shutil.copy2(db, bak)
            print(f"  Backup: {bak}")
        for cid in dead_ids:
            if args.deactivate:
                con.execute("UPDATE providerConnections SET isActive = 0 WHERE id = ?", (cid,))
            else:
                con.execute("DELETE FROM providerConnections WHERE id = ?", (cid,))
        con.commit()
        verb = "deactivated" if args.deactivate else "removed"
        print(f"  ✓ {verb} {len(dead_ids)} dead connection(s)")
    elif dead_ids and not args.prune:
        print("  (run with --prune to remove the dead ones)")

    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
