#!/usr/bin/env python3
"""scripts/zcode_inject_9router.py — ensure every ZCode bridge key has a 9Router
providerConnection row.

Minting a JWT only makes the bridge able to serve an account; 9Router also needs
a connection row (name Zcode<N>, provider = the Zcode node id, apiKey = the
bridge key from ~/.zcode-bridge/keys.txt). Without it the account is invisible
in the dashboard and unused. Idempotent: matches on the apiKey.

  python3 scripts/zcode_inject_9router.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import uuid
from pathlib import Path

DB = Path(os.path.expanduser("~/.9router/db/data.sqlite"))
STATE = Path(os.path.expanduser("~/.zcode-bridge"))
BASE = "http://127.0.0.1:8787/v1"
PREFIX = "Zcode"
NODE_NAME = "Zcode"
DEFAULT_MODEL = "GLM-5.3"


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000Z"


def keys_map() -> list[tuple[str, str]]:
    """[(key, email)] from keys.txt, in file order."""
    out = []
    f = STATE / "keys.txt"
    if not f.exists():
        return out
    for line in f.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out.append((k.strip(), v.strip()))
    return out


def jwt_ok(email: str) -> bool:
    p = STATE / "jwt" / (email + ".txt")
    return p.exists() and bool(p.read_text().strip())


def find_node(con) -> str:
    for nid, data in con.execute("SELECT id, data FROM providerNodes"):
        try:
            if json.loads(data or "{}").get("prefix") == PREFIX:
                return nid
        except Exception:
            pass
    raise SystemExit("no providerNodes row with prefix %r — create the Zcode node first" % PREFIX)


def next_name(con, node_id: str, pending: set) -> str:
    used = set(pending)
    for (name,) in con.execute("SELECT name FROM providerConnections WHERE provider = ?", (node_id,)):
        m = re.fullmatch(r"Zcode(\d+)", name or "")
        if m:
            used.add(int(m.group(1)))
    n = 1
    while n in used:
        n += 1
    pending.add(n)
    return "Zcode%d" % n


def main() -> int:
    ap = argparse.ArgumentParser(description="Inject ZCode bridge keys into 9Router")
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if not Path(a.db).exists():
        print("✗ 9Router DB not found:", a.db, file=sys.stderr)
        return 1
    con = sqlite3.connect(a.db)
    node_id = find_node(con)
    rows = list(con.execute("SELECT id, name, data FROM providerConnections WHERE provider = ?", (node_id,)))
    blob = "\n".join(r[2] or "" for r in rows)
    km = keys_map()

    # Emails already represented by ANY key in the DB (match each row's apiKey
    # back to keys.txt). One 9Router row per account, even if it has >1 key.
    covered = set()
    for _, _, data in rows:
        for key, email in km:
            if key and key in (data or ""):
                covered.add(email)

    added = updated = 0
    pending: set = set()
    for key, email in km:
        if key in blob:
            for cid, name, data in rows:
                if key in (data or ""):
                    d = json.loads(data)
                    if d.get("testStatus") != "active" or not d.get("apiKey"):
                        d["testStatus"] = "active"
                        if not a.dry_run:
                            con.execute("UPDATE providerConnections SET data=?, isActive=1, updatedAt=? WHERE id=?",
                                        (json.dumps(d), now_iso(), cid))
                        print("  ~ refreshed", name)
                        updated += 1
            continue
        if email in covered:
            print("  = skip     %s (already has a connection)" % email)
            continue
        name = next_name(con, node_id, pending)
        psd = {"prefix": PREFIX, "apiType": "chat", "baseUrl": BASE, "nodeName": NODE_NAME,
               "connectionProxyEnabled": False, "connectionProxyUrl": "", "connectionNoProxy": ""}
        data = {"defaultModel": DEFAULT_MODEL, "apiKey": key, "testStatus": "active",
                "providerSpecificData": psd, "errorCode": None, "backoffLevel": 0,
                "modelLock_abliterated": None, "modelLock_GLM-5.3-Flash": None}
        ts = now_iso()
        if not a.dry_run:
            con.execute(
                "INSERT INTO providerConnections (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), node_id, "apikey", name, None, 1, 1, json.dumps(data), ts, ts))
        flag = "" if jwt_ok(email) else "  (no JWT yet — bridge will 401 until minted)"
        print("  + %-9s %s%s" % (name, email, flag))
        added += 1

    if not a.dry_run:
        con.commit()
    print("zcode inject: +%d added, %d refreshed%s" % (added, updated, " (dry-run)" if a.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
