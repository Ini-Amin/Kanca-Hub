#!/usr/bin/env python3
"""
Inject free-provider API keys into 9Router.

One upsert path (ensure node -> upsert one providerConnections row per key ->
backup the DB), two key sources selected with --kind:

  thk    TokenHarbor keys. Source: harbor/account.json (api_key is `thk_live_...`).
         Node: openai-compatible "TokenHarbor", base https://tokenharbor.ai/v1
  zcode  ZCode bridge keys. Source: ~/.zcode-bridge/keys.txt (`key=email`).
         Node: openai-compatible "Zcode", base http://127.0.0.1:8787/v1
         One row per ACCOUNT even when an account has several bridge keys.

Usage:
    python3 inject_thk_9router.py                        # thk, default account.json
    python3 inject_thk_9router.py -i account.json --verify --dry-run
    python3 inject_thk_9router.py --kind zcode           # every ZCode bridge account
    python3 inject_thk_9router.py --kind zcode --dry-run
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
REPO = Path(__file__).resolve().parent.parent


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# ─────────────────────────────────────────────────────────────── key sources
def load_harbor(path: str) -> list[dict]:
    """[(key, name)] from harbor's account.json."""
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):
        # harbor's _save_account writes a bare single-account object when the
        # file did not previously exist; accept that as well as wrapped lists.
        if data.get("api_key") or data.get("key"):
            data = [data]
        else:
            data = data.get("accounts") or data.get("results") or []
    out = []
    for i, a in enumerate([x for x in data if isinstance(x, dict)], 1):
        k = (a.get("api_key") or a.get("key") or "").strip()
        if k.startswith("thk_"):
            out.append({"key": k, "name": a.get("email") or f"thk-{i}", "email": a.get("email")})
    return out


def load_zcode(path: str) -> list[dict]:
    """[(key, email)] from ~/.zcode-bridge/keys.txt (`key=email`)."""
    out = []
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, email = line.split("=", 1)
            out.append({"key": k.strip(), "name": email.strip(), "email": email.strip()})
    return out


def verify_http(base: str, key: str, model: str, timeout: int = 30) -> tuple[bool, str]:
    url = f"{base}/chat/completions"
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


SOURCES = {
    "thk": {
        "node_id": "openai-compatible-chat-1d39647b-193d-4f65-b38b-03d80c92460a",
        "node_name": "TokenHarbor",
        "prefix": "THK",
        "base": "https://tokenharbor.ai/v1",
        "model": "deepseek-v4.1-flash:free",
        "default_input": str(REPO / "harbor" / "account.json"),
        "load": load_harbor,
        "verify": True,
        "one_per_email": False,
    },
    "zcode": {
        "node_id": "openai-compatible-chat-357dffc2-100b-4534-86e4-78cab29ba14b",
        "node_name": "Zcode",
        "prefix": "Zcode",
        "base": "http://127.0.0.1:8787/v1",
        "model": "GLM-5.3",
        "default_input": str(Path.home() / ".zcode-bridge" / "keys.txt"),
        "load": load_zcode,
        "verify": False,          # the bridge 401s for an unminted account; no cheap probe
        "one_per_email": True,    # an account may hold >1 bridge key; keep one row
        # every account is a separate connection row: rotate across them so one
        # client cannot pile onto a single account (which 3009s on concurrency)
        "round_robin": True,
    },
    "zcode-janus": {
        # Janus's dedicated bridge (:8788, own egress). Same keys, separate
        # node so Janus's quota burn and failover never touch the main pool.
        "node_id": "openai-compatible-chat-9f2c1a7e-7d4d-4e9a-b3f1-2c8d4e6a0f1b",
        "node_name": "ZcodeJanus",
        "prefix": "ZcodeJ",
        "base": "http://127.0.0.1:8788/v1",
        "model": "GLM-5.3",
        "default_input": str(Path.home() / ".zcode-bridge" / "keys.txt"),
        "load": load_zcode,
        "verify": False,
        "one_per_email": True,
        "round_robin": True,
    },
}


# ─────────────────────────────────────────────────────────────── db helpers
def backup_db(db: Path, tag: str) -> Path | None:
    if not db.exists():
        return None
    dest = db.with_suffix(f".sqlite.{tag}-{time.strftime('%Y%m%d-%H%M%S')}.bak")
    shutil.copy2(db, dest)
    return dest


def ensure_node(con: sqlite3.Connection, src: dict, dry_run: bool) -> str:
    """Ensure the source's provider node exists; return its id."""
    cur = con.cursor()
    if cur.execute("SELECT id FROM providerNodes WHERE id = ?", (src["node_id"],)).fetchone():
        return src["node_id"]
    for nid, data in cur.execute("SELECT id, data FROM providerNodes"):
        try:
            if json.loads(data or "{}").get("prefix") == src["prefix"]:
                return nid
        except Exception:  # noqa: BLE001
            pass
    ts = now_iso()
    node_data = {"prefix": src["prefix"], "apiType": "chat", "baseUrl": src["base"]}
    if not dry_run:
        cur.execute(
            "INSERT INTO providerNodes (id, type, name, data, createdAt, updatedAt) VALUES (?,?,?,?,?,?)",
            (src["node_id"], "openai-compatible", src["node_name"], json.dumps(node_data), ts, ts),
        )
    print(f"  + node created: {src['node_name']}")
    return src["node_id"]


def _psd(src: dict) -> dict:
    return {"prefix": src["prefix"], "apiType": "chat", "baseUrl": src["base"],
            "nodeName": src["node_name"], "connectionProxyEnabled": False,
            "connectionProxyUrl": "", "connectionNoProxy": ""}


def upsert(con: sqlite3.Connection, node_id: str, src: dict, key: str, name: str,
           dry_run: bool) -> str:
    """Insert or refresh one connection. Returns "updated" or "inserted"."""
    cur = con.cursor()
    ts = now_iso()
    data = {"defaultModel": src["model"], "apiKey": key, "testStatus": "active",
            "providerSpecificData": _psd(src), "errorCode": None, "backoffLevel": 0,
            f"modelLock_{src['model']}": None}
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
        return "updated"
    new_id = str(uuid.uuid4())
    if not dry_run:
        cur.execute(
            "INSERT INTO providerConnections (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (new_id, node_id, "apikey", name, None, 1, 1, json.dumps(data), ts, ts),
        )
    print(f"  + inserted {name}")
    return "inserted"


def next_name(con: sqlite3.Connection, node_id: str, src: dict, pending: set) -> str:
    """Zcode1, Zcode2, ... — the first free N (skipping ones we are about to add)."""
    import re
    used = set(pending)
    for (name,) in con.execute("SELECT name FROM providerConnections WHERE provider = ?", (node_id,)):
        m = re.fullmatch(re.escape(src["prefix"]) + r"(\d+)", name or "")
        if m:
            used.add(int(m.group(1)))
    n = 1
    while n in used:
        n += 1
    pending.add(n)
    return "%s%d" % (src["prefix"], n)


def ensure_round_robin(con: sqlite3.Connection, src: dict, dry_run: bool) -> bool:
    """Turn on round-robin for this provider's connections in 9Router settings.

    9Router keeps the per-provider strategy in settings.data.providerStrategies
    (the dashboard's PATCH /api/settings). With one connection row per account,
    'fallback' pins the first row and 3009s under concurrency; round-robin
    spreads load across accounts. Idempotent."""
    if not src.get("round_robin"):
        return False
    node = src["node_id"]
    cur = con.cursor()
    row = cur.execute("SELECT data FROM settings WHERE id = 1").fetchone()
    try:
        data = json.loads(row[0]) if row and row[0] else {}
    except Exception:  # noqa: BLE001
        data = {}
    want = {"fallbackStrategy": "round-robin", "stickyRoundRobinLimit": 1}
    have = (data.get("providerStrategies") or {}).get(node)
    if have == want:
        print(f"  = round-robin already on for {src['node_name']}")
        return False
    data.setdefault("providerStrategies", {})[node] = want
    if not dry_run:
        if row:
            cur.execute("UPDATE settings SET data=? WHERE id=1", (json.dumps(data),))
        else:
            cur.execute("INSERT INTO settings (id, data) VALUES (1, ?)", (json.dumps(data),))
    print(f"  + round-robin on for {src['node_name']}")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Inject free-provider keys into 9Router")
    ap.add_argument("--kind", choices=sorted(SOURCES), default="thk",
                    help="which key source (default: thk)")
    ap.add_argument("-i", "--input", default=None, help="key source path (default per --kind)")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--model", default=None, help="override the default model")
    ap.add_argument("--verify", action="store_true", help="probe each key before inserting")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = dict(SOURCES[args.kind])
    if args.model:
        src["model"] = args.model
    inp = args.input or src["default_input"]

    if not Path(args.db).exists():
        print(f"✗ 9Router DB not found: {args.db}", file=sys.stderr)
        return 1
    if not Path(inp).exists():
        print(f"✗ key source not found: {inp}", file=sys.stderr)
        return 1

    entries = src["load"](inp)
    print(f"[{args.kind}] loaded {len(entries)} key(s) from {inp}")
    if not entries:
        print("Nothing to inject.")
        return 0

    if args.verify and src["verify"]:
        print(f"Verifying keys against {src['node_name']}…")
        kept = []
        for e in entries:
            ok, detail = verify_http(src["base"], e["key"], src["model"])
            print(f"  {'✅' if ok else '❌'} {(e.get('email') or '?'):34s} {detail}")
            if ok:
                kept.append(e)
        entries = kept
        if not entries:
            print("No keys passed verification.")
            return 1

    if not args.dry_run:
        b = backup_db(Path(args.db), args.kind)
        if b:
            print(f"Backup: {b}")

    con = sqlite3.connect(args.db)
    added = updated = skipped = 0
    try:
        con.execute("PRAGMA journal_mode=WAL")
        node_id = ensure_node(con, src, args.dry_run)
        ensure_round_robin(con, src, args.dry_run)

        # one_per_email: a key whose account already has a row is not a new row
        covered = set()
        if src["one_per_email"]:
            by_key = {e["key"]: e.get("email") for e in entries}
            for (_, cdata) in con.execute(
                    "SELECT id, data FROM providerConnections WHERE provider = ?", (node_id,)):
                for k, email in by_key.items():
                    if k in (cdata or ""):
                        covered.add(email)

        pending: set = set()
        for e in entries:
            if src["one_per_email"] and e.get("email") in covered:
                print(f"  = skip     {e['email']} (already has a connection)")
                skipped += 1
                continue
            name = e["name"] if not src["one_per_email"] else next_name(con, node_id, src, pending)
            if upsert(con, node_id, src, e["key"], name, args.dry_run) == "inserted":
                added += 1
            else:
                updated += 1
        if not args.dry_run:
            con.commit()
    finally:
        con.close()

    verb = "would inject" if args.dry_run else "Injected"
    print(f"\n{verb} {added} new / {updated} refreshed / {skipped} skipped "
          f"{args.kind} key(s) into 9Router (node {src['node_name']}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
