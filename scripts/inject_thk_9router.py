#!/usr/bin/env python3
"""
Inject TokenHarbor API keys (from harbor/tokenharbor) into 9Router.

9Router ships an openai-compatible "TokenHarbor" provider node:
    id   : openai-compatible-chat-1d39647b-193d-4f65-b38b-03d80c92460a
    data : {"prefix":"THK","apiType":"chat","baseUrl":"https://tokenharbor.ai/v1"}

This reads harbor's account.json (list of {email,password,api_key,...} where
api_key is `thk_live_...`) and upserts one providerConnections row per key.

Usage:
    python3 inject_thk_9router.py -i ~/harbor/account.json
    python3 inject_thk_9router.py -i account.json --verify --dry-run
    python3 inject_thk_9router.py -i account.json --model deepseek-v4.1-flash:free
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

THK_NODE_ID = "openai-compatible-chat-1d39647b-193d-4f65-b38b-03d80c92460a"
THK_NODE_NAME = "TokenHarbor"
THK_PREFIX = "THK"
THK_BASE = "https://tokenharbor.ai/v1"
THK_DEFAULT_MODEL = "deepseek-v4.1-flash:free"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def load_accounts(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):
        # harbor's _save_account writes a bare single-account object when the
        # file did not previously exist; accept that as well as wrapped lists.
        if data.get("api_key") or data.get("key"):
            data = [data]
        else:
            data = data.get("accounts") or data.get("results") or []
    return [a for a in data if isinstance(a, dict)]


def valid_keys(accounts: list[dict]) -> list[dict]:
    out = []
    for a in accounts:
        k = (a.get("api_key") or a.get("key") or "").strip()
        if k.startswith("thk_"):
            out.append(a)
    return out


def verify_key(key: str, model: str, timeout: int = 30) -> tuple[bool, str]:
    url = f"{THK_BASE}/chat/completions"
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "hi"}],
                       "max_tokens": 1}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return False, type(e).__name__


def backup_db(db: Path) -> Path | None:
    if not db.exists():
        return None
    dest = db.with_suffix(f".sqlite.thk-{time.strftime('%Y%m%d-%H%M%S')}.bak")
    shutil.copy2(db, dest)
    return dest


def ensure_node(con: sqlite3.Connection, dry_run: bool) -> str:
    """Ensure the TokenHarbor node exists; return its id."""
    cur = con.cursor()
    row = cur.execute("SELECT id FROM providerNodes WHERE id = ?", (THK_NODE_ID,)).fetchone()
    if row:
        return THK_NODE_ID
    # try to find any node with the same prefix
    for nid, data in cur.execute("SELECT id, data FROM providerNodes"):
        try:
            if json.loads(data or "{}").get("prefix") == THK_PREFIX:
                return nid
        except Exception:
            pass
    ts = now_iso()
    node_data = {"prefix": THK_PREFIX, "apiType": "chat", "baseUrl": THK_BASE}
    if not dry_run:
        cur.execute(
            "INSERT INTO providerNodes (id, type, name, data, createdAt, updatedAt) VALUES (?,?,?,?,?,?)",
            (THK_NODE_ID, "openai-compatible", THK_NODE_NAME, json.dumps(node_data), ts, ts),
        )
    print(f"  + node created: {THK_NODE_NAME}")
    return THK_NODE_ID


def upsert(con: sqlite3.Connection, node_id: str, key: str, name: str,
           model: str, dry_run: bool) -> str:
    cur = con.cursor()
    ts = now_iso()
    psd = {"prefix": THK_PREFIX, "apiType": "chat", "baseUrl": THK_BASE, "nodeName": THK_NODE_NAME,
           "connectionProxyEnabled": False, "connectionProxyUrl": "", "connectionNoProxy": ""}
    data = {"defaultModel": model, "apiKey": key, "testStatus": "active",
            "providerSpecificData": psd, "errorCode": None, "backoffLevel": 0,
            f"modelLock_{model}": None}

    existing = None
    for cid, cdata in cur.execute("SELECT id, data FROM providerConnections WHERE provider = ?", (node_id,)):
        if key in (cdata or ""):
            existing = cid
            break
    if existing:
        if not dry_run:
            cur.execute("UPDATE providerConnections SET data=?, isActive=1, name=?, updatedAt=? WHERE id=?",
                        (json.dumps(data), name, ts, existing))
        print(f"  ~ updated  {name}")
        return existing
    new_id = str(uuid.uuid4())
    if not dry_run:
        cur.execute(
            "INSERT INTO providerConnections (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (new_id, node_id, "apikey", name, None, 1, 1, json.dumps(data), ts, ts),
        )
    print(f"  + inserted {name}")
    return new_id


def main() -> int:
    ap = argparse.ArgumentParser(description="Inject TokenHarbor keys into 9Router")
    ap.add_argument("-i", "--input", default=str(Path.home() / "harbor" / "account.json"),
                    help="harbor account.json")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--model", default=THK_DEFAULT_MODEL)
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not Path(args.db).exists():
        print(f"✗ 9Router DB not found: {args.db}", file=sys.stderr)
        return 1
    if not Path(args.input).exists():
        print(f"✗ account.json not found: {args.input}", file=sys.stderr)
        return 1

    accounts = load_accounts(args.input)
    keys = valid_keys(accounts)
    print(f"Loaded {len(accounts)} accounts, {len(keys)} with thk_ keys.")
    if not keys:
        print("Nothing to inject.")
        return 0

    if args.verify:
        print("Verifying keys against TokenHarbor…")
        kept = []
        for a in keys:
            k = a.get("api_key") or a["key"]
            ok, detail = verify_key(k, args.model)
            print(f"  {'✅' if ok else '❌'} {a.get('email','?'):34s} {detail}")
            kept.append(a) if ok else None
        keys = kept
        if not keys:
            print("No keys passed verification.")
            return 1

    if not args.dry_run:
        b = backup_db(Path(args.db))
        if b:
            print(f"Backup: {b}")

    con = sqlite3.connect(args.db)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        node_id = ensure_node(con, args.dry_run)
        for i, a in enumerate(keys, 1):
            key = a.get("api_key") or a["key"]
            name = a.get("email") or f"thk-{i}"
            upsert(con, node_id, key, name, args.model, args.dry_run)
        if not args.dry_run:
            con.commit()
    finally:
        con.close()

    verb = "would inject" if args.dry_run else "Injected"
    print(f"\n{verb} {len(keys)} TokenHarbor key(s) into 9Router (provider {THK_NODE_NAME}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
