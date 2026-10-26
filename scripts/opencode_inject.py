#!/usr/bin/env python3
"""Inject OpenCode Zen API keys into 9router as provider connections.

WHY THIS IS A BATCH TOOL, NOT A ONE-LINER
-----------------------------------------
`kancahub opencode inject --api-key K` handles one key by hand. A farm produces
dozens, and each must become its own `providerConnections` row so 9router's own
failover rotates accounts instead of retrying one dead key.

READ THIS BEFORE TRUSTING THE RESULT
------------------------------------
A Zen **free-tier** key does not work as a raw OpenAI-compatible endpoint.
Measured on this box 2026-10-22 against the endpoint the catalog declares
(https://opencode.ai/inference/openai/v1):

    POST /chat/completions  ->  403
    {"type":"error","error":{"type":"FreeTierError","message":"OpenCode's free
     tier can only be used from within OpenCode."}}

That is not a path or header mistake: the catalog's own provider entry names
`https://opencode.ai/inference/openai/v1` and lists `OPENCODE_API_KEY` in `env`,
and the request still refuses. It is a request-SHAPE check, so curl and plain
HTTP clients always lose it.

BUT the key itself is live and valuable. Measured 2026-10-22: with
`auth.json` set to {"opencode": {"type": "api", "key": "oc_sk_..."}}, the real
CLI answered 20/20 requests on `opencode/glm-5.3-flash-free` with no 429, while
the anonymous (no-key) path was returning FreeUsageLimitError on every call.
Two different `oc_sk_` keys both worked, 4/4 each. So a key is what buys
headroom -- it just has to be spent through the CLI, not through curl.

Practical consequence: inject keys here so 9router has them on file, but the
serving path must be the local daemon (`scripts/opencode_bridge.py`), which
drives the real binary. A 403 from the probe below is EXPECTED and does not
mean the key is dead; use `opencode_farm.py verify --key ...` for a real verdict.

USAGE
    opencode_inject.py --from-auth-json ~/.local/share/opencode/auth.json
    opencode_inject.py --key sk-... --key sk-...
    opencode_inject.py --from-file keys.txt          # one key per line
    opencode_inject.py --from-json farm.json         # [{"apiKey": "..."}] or {"key": "..."}
    ... add --dry-run to print the plan and write nothing.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import uuid
from pathlib import Path

ZEN_BASE = "https://opencode.ai/inference/openai/v1"
ZEN_MODELS = [
    "glm-5.3-flash-free", "mimo-v2.6-flash-free",
    "nemotron-3.5-lightning-free", "minimax-m3.1-flash-free",
]
NODE_NAME = "OpenCodeZen"
NODE_ID = "openai-compatible-chat-opencode-zen"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def db_path() -> Path:
    return Path.home() / ".9router" / "db" / "data.sqlite"


def collect(args) -> list[tuple[str, str]]:
    """Return [(label, key)] from every source the caller supplied."""
    out: list[tuple[str, str]] = []

    for k in args.key or []:
        if k.strip():
            out.append(("cli", k.strip()))

    if args.from_file:
        p = Path(args.from_file).expanduser()
        if p.is_file():
            for line in p.read_text(errors="replace").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    out.append((p.name, line))

    if args.from_json:
        p = Path(args.from_json).expanduser()
        if p.is_file():
            raw = p.read_text(errors="replace")
            items = []
            try:
                d = json.loads(raw)
                items = d if isinstance(d, list) else [d]
            except ValueError:
                # Fall back to jsonl (e.g. reports/hcaptcha_review/api_keys.jsonl)
                for line in raw.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        try:
                            items.append(json.loads(line))
                        except Exception:
                            pass
            for it in items:
                if isinstance(it, str):
                    out.append((p.name, it))
                elif isinstance(it, dict):
                    key = (it.get("apiKey") or it.get("key") or
                           it.get("token") or it.get("api_key"))
                    label = it.get("email") or it.get("name") or p.name
                    if isinstance(key, str) and key.strip():
                        out.append((label, key.strip()))
    if args.from_auth_json:
        p = Path(args.from_auth_json).expanduser()
        if p.is_file():
            try:
                d = json.loads(p.read_text(errors="replace"))
            except ValueError:
                d = {}
            for provider, rec in (d or {}).items():
                if isinstance(rec, dict):
                    key = rec.get("key")
                    if isinstance(key, str) and key.strip():
                        out.append((provider, key.strip()))

    # de-dup by key, keep first label
    seen, uniq = set(), []
    for label, key in out:
        if key not in seen:
            seen.add(key)
            uniq.append((label, key))
    return uniq


def ensure_node(con: sqlite3.Connection, dry: bool) -> None:
    row = con.execute("SELECT id FROM providerNodes WHERE id=?", (NODE_ID,)).fetchone()
    if row:
        return
    data = json.dumps({
        "baseUrl": ZEN_BASE,
        "apiType": "openai",
        "models": ZEN_MODELS,
    })
    if not dry:
        con.execute(
            "INSERT INTO providerNodes (id, type, name, data, createdAt, updatedAt)"
            " VALUES (?,?,?,?,?,?)",
            (NODE_ID, "openai-compatible", NODE_NAME, data, now_iso(), now_iso()))
    print("  node %s: %s" % (NODE_NAME, "would create" if dry else "created"))


def next_name(con: sqlite3.Connection, pending: set[str]) -> str:
    used = set(pending)
    for (name,) in con.execute("SELECT name FROM providerConnections WHERE provider=?", (NODE_ID,)):
        if name and name.startswith(NODE_NAME):
            tail = name[len(NODE_NAME):]
            if tail.isdigit():
                used.add(tail)
    n = 1
    while str(n) in used:
        n += 1
    pending.add(str(n))
    return "%s%d" % (NODE_NAME, n)


def upsert(con: sqlite3.Connection, label: str, key: str, dry: bool) -> str:
    cur = con.cursor()
    data = {
        "defaultModel": ZEN_MODELS[0],
        "apiKey": key,
        "testStatus": "unverified",
        "providerSpecificData": {},
        "errorCode": None,
        "backoffLevel": 0,
    }
    existing = None
    for cid, cdata in cur.execute(
            "SELECT id, data FROM providerConnections WHERE provider=?", (NODE_ID,)):
        if key in (cdata or ""):
            existing = cid
            break
    if existing:
        if not dry:
            cur.execute("UPDATE providerConnections SET data=?, isActive=1, name=?, updatedAt=? WHERE id=?",
                        (json.dumps(data), label[:40], now_iso(), existing))
        return "updated"
    if not dry:
        cur.execute(
            "INSERT INTO providerConnections (id, provider, authType, name, email, priority,"
            " isActive, data, createdAt, updatedAt) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), NODE_ID, "apikey", label[:40], None, 10, 1,
             json.dumps(data), now_iso(), now_iso()))
    return "inserted"


def main() -> int:
    ap = argparse.ArgumentParser(description="Inject OpenCode Zen keys into 9router")
    ap.add_argument("--key", action="append", help="a single key (repeatable)")
    ap.add_argument("--from-file", help="file with one key per line")
    ap.add_argument("--from-json", help="JSON array/object holding apiKey/key/token")
    ap.add_argument("--from-auth-json", help="opencode auth.json to read provider keys from")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    ap.add_argument("--db", default=None, help="9router sqlite path (default ~/.9router/db/data.sqlite)")
    a = ap.parse_args()

    keys = collect(a)
    if not keys:
        print("no keys found in the sources you gave", file=sys.stderr)
        return 1
    print("keys to inject: %d" % len(keys))

    db = Path(a.db).expanduser() if a.db else db_path()
    if not db.is_file():
        print("9router db not found: %s" % db, file=sys.stderr)
        return 1

    con = sqlite3.connect(str(db))
    try:
        ensure_node(con, a.dry_run)
        pending: set[str] = set()
        counts = {"inserted": 0, "updated": 0}
        for label, key in keys:
            name = next_name(con, pending)
            action = upsert(con, name, key, a.dry_run)
            counts[action] += 1
            print("  %-9s %-16s %s…%s" % (action, name, key[:8], key[-4:]))
        if not a.dry_run:
            con.commit()
        print("\ninserted=%d updated=%d%s" % (
            counts["inserted"], counts["updated"],
            " (DRY RUN — nothing written)" if a.dry_run else ""))
    finally:
        con.close()

    print("\nNOTE: a Zen free-tier key still answers 403 FreeTierError as an"
          "\nOpenAI-compatible endpoint (measured 2026-10-22 on"
          "\n%s). These rows only produce traffic for a paid key." % ZEN_BASE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
