#!/usr/bin/env python3
"""
Inject Cloudflare Workers AI tokens from Auto-FreeCF results into 9Router.

9Router (https://github.com/9router) ships a BUILT-IN provider:
    id:     "cloudflare-ai"   (alias "cf")
    auth:   apikey
    data:   providerSpecificData.accountId + apiKey (cfut_...)

This script writes those connections into 9Router's SQLite DB:
    ~/.9router/db/data.sqlite  ->  providerConnections

No custom provider node is needed — 9Router already knows cloudflare-ai.

Usage:
    python3 inject_9router.py                        # inject all valid keys (default results.json)
    python3 inject_9router.py -i results2.json
    python3 inject_9router.py --verify               # test each key against Cloudflare first
    python3 inject_9router.py --dry-run
    python3 inject_9router.py --model @cf/openai/gpt-oss-120b

Safe: writes a timestamped backup of data.sqlite before modifying.
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
import uuid
from datetime import datetime, timezone
from pathlib import Path

NINE_ROUTER_HOME = Path(os.environ.get("NINE_ROUTER_HOME", Path.home() / ".9router"))
DB_PATH = NINE_ROUTER_HOME / "db" / "data.sqlite"

CF_PROVIDER = "cloudflare-ai"
CF_DEFAULT_MODEL = "@cf/openai/gpt-oss-120b"
CF_CHAT_URL = "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def load_results(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    if isinstance(data, dict):
        data = data.get("results") or data.get("accounts") or []
    return [r for r in data if isinstance(r, dict)]


def valid_tokens(results: list[dict]) -> list[dict]:
    out = []
    for r in results:
        token = (r.get("api_token") or "").strip()
        account_id = (r.get("account_id") or "").strip()
        if token.startswith("cfut_") and account_id:
            out.append(r)
    return out


def verify_cloudflare_key(account_id: str, token: str, model: str, timeout: int = 30) -> tuple[bool, str]:
    url = CF_CHAT_URL.format(account_id=account_id)
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status == 200, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def backup_db(db: Path) -> Path | None:
    if not db.exists():
        return None
    dest = db.with_suffix(f".sqlite.inject-{time.strftime('%Y%m%d-%H%M%S')}.bak")
    shutil.copy2(db, dest)
    return dest


def existing_connection(con: sqlite3.Connection, account_id: str) -> str | None:
    """Return the id of an existing cloudflare-ai connection for this account, if any."""
    for cid, data in con.execute(
        "SELECT id, data FROM providerConnections WHERE provider = ?", (CF_PROVIDER,)
    ):
        if account_id in (data or ""):
            return cid
    return None


def upsert_connection(con: sqlite3.Connection, account_id: str, token: str,
                      name: str, model: str, dry_run: bool) -> str:
    cur = con.cursor()
    ts = now_iso()
    provider_specific = {
        "accountId": account_id,
        "connectionProxyEnabled": False,
        "connectionProxyUrl": "",
        "connectionNoProxy": "",
    }
    data = {
        "defaultModel": model,
        "apiKey": token,
        "testStatus": "active",
        "providerSpecificData": provider_specific,
        "errorCode": None,
        "backoffLevel": 0,
        f"modelLock_{model}": None,
    }

    cid = existing_connection(con, account_id)
    if cid:
        if not dry_run:
            cur.execute(
                "UPDATE providerConnections SET data = ?, isActive = 1, name = ?, updatedAt = ? WHERE id = ?",
                (json.dumps(data), name, ts, cid),
            )
        print(f"  ~ updated  {name}  (account {account_id[:8]}…)")
        return cid

    new_id = str(uuid.uuid4())
    if not dry_run:
        cur.execute(
            "INSERT INTO providerConnections "
            "(id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (new_id, CF_PROVIDER, "apikey", name, None, 1, 1, json.dumps(data), ts, ts),
        )
    print(f"  + inserted {name}  (account {account_id[:8]}…)")
    return new_id


def main() -> int:
    ap = argparse.ArgumentParser(description="Inject Cloudflare Workers AI keys into 9Router")
    ap.add_argument("-i", "--input", default="results.json", help="Auto-FreeCF results JSON")
    ap.add_argument("--db", default=str(DB_PATH), help=f"9Router DB (default: {DB_PATH})")
    ap.add_argument("--model", default=CF_DEFAULT_MODEL, help=f"default model (default: {CF_DEFAULT_MODEL})")
    ap.add_argument("--verify", action="store_true", help="verify each key against Cloudflare first")
    ap.add_argument("--dry-run", action="store_true", help="show actions without writing")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"✗ 9Router DB not found: {db}", file=sys.stderr)
        print("  Is 9Router installed and started at least once?", file=sys.stderr)
        return 1

    results_path = Path(args.input)
    if not results_path.is_absolute() and not results_path.exists():
        alt = Path(__file__).resolve().parent.parent / "signup_from_scratch" / args.input
        if alt.exists():
            results_path = alt
    if not results_path.exists():
        print(f"✗ results file not found: {args.input}", file=sys.stderr)
        return 1

    results = load_results(results_path)
    keys = valid_tokens(results)
    print(f"Loaded {len(results)} results, {len(keys)} with valid (account_id, cfut_ token).")
    if not keys:
        print("Nothing to inject.")
        return 0

    if args.verify:
        print("Verifying keys against Cloudflare…")
        kept = []
        for r in keys:
            ok, detail = verify_cloudflare_key(r["account_id"], r["api_token"], args.model)
            print(f"  {'✅' if ok else '❌'} {r.get('email','?')} ({r['account_id'][:8]}…) {detail}")
            if ok:
                kept.append(r)
        keys = kept
        if not keys:
            print("No keys passed verification.")
            return 1

    print("DRY RUN — no changes will be written." if args.dry_run else "", end="")
    if not args.dry_run:
        b = backup_db(db)
        if b:
            print(f"Backup: {b}")

    con = sqlite3.connect(db)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        for r in keys:
            name = r.get("email") or f"cf-{r['account_id'][:8]}"
            upsert_connection(con, r["account_id"], r["api_token"], name, args.model, args.dry_run)
        if not args.dry_run:
            con.commit()
    finally:
        con.close()

    verb = "would inject" if args.dry_run else "Injected"
    print(f"\n{verb} {len(keys)} Cloudflare Workers AI key(s) into 9Router (provider: {CF_PROVIDER}).")
    if not args.dry_run:
        print("9Router reads the DB live. If the new provider doesn't show up, restart it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
