#!/usr/bin/env python3
"""
scripts/inject_9router_proxy.py — add/update a proxy in 9Router's proxy pool.

9Router stores outbound proxies in the `proxyPools` table: columns
(id, isActive, testStatus, createdAt, updatedAt) + a `data` JSON blob holding
{name, proxyUrl, noProxy, type, strictProxy}. A provider connection opts in via
providerSpecificData.proxyPoolId.

This upserts one pool from a proxy URL so the CLI can register egresses without
the web UI. It mirrors src/lib/db/repos/proxyPoolsRepo.js.

Usage:
  python scripts/inject_9router_proxy.py add "http://user:pass@host:port" [--name X]
  python scripts/inject_9router_proxy.py list
  python scripts/inject_9router_proxy.py add-file proxies.txt --name webshare
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB = Path(os.environ.get("NINE_ROUTER_HOME", Path.home() / ".9router")) / "db" / "data.sqlite"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def add_pool(db_path: Path, proxy_url: str, name: str = "", ptype: str = "http",
             strict: bool = False, is_active: bool = True, pool_id: str = "") -> str:
    if "://" not in proxy_url:
        proxy_url = "http://" + proxy_url
    pid = pool_id or str(uuid.uuid4())
    data = {"name": name or proxy_url.split("@")[-1], "proxyUrl": proxy_url,
            "noProxy": "", "type": ptype, "strictProxy": bool(strict)}
    now = _now()
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            """INSERT INTO proxyPools(id, isActive, testStatus, data, createdAt, updatedAt)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET isActive=excluded.isActive,
                 data=excluded.data, updatedAt=excluded.updatedAt""",
            (pid, 1 if is_active else 0, "unknown", json.dumps(data), now, now))
        con.commit()
    finally:
        con.close()
    return pid


def list_pools(db_path: Path) -> list[dict]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("SELECT id,isActive,testStatus,data,updatedAt FROM proxyPools").fetchall()
    finally:
        con.close()
    out = []
    for r in rows:
        try:
            d = json.loads(r[3] or "{}")
        except Exception:
            d = {}
        out.append({"id": r[0], "isActive": bool(r[1]), "testStatus": r[2],
                    "name": d.get("name"), "proxyUrl": d.get("proxyUrl"), "type": d.get("type")})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Add/manage 9Router proxy pools")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add"); a.add_argument("proxy_url"); a.add_argument("--name", default="")
    a.add_argument("--type", default="http"); a.add_argument("--strict", action="store_true")
    af = sub.add_parser("add-file"); af.add_argument("file"); af.add_argument("--name", default="")
    af.add_argument("--type", default="http")
    sub.add_parser("list")
    args = ap.parse_args(argv)

    if not DB.exists():
        print(f"  ✗ 9Router DB not found: {DB}")
        return 1
    if args.cmd == "add":
        pid = add_pool(DB, args.proxy_url, args.name, args.type, args.strict)
        print(f"  ✓ added pool {pid}  {args.name or args.proxy_url}")
    elif args.cmd == "add-file":
        lines = [l.strip() for l in Path(args.file).read_text().splitlines() if l.strip() and not l.startswith("#")]
        n = 0
        for i, url in enumerate(lines, 1):
            add_pool(DB, url, f"{args.name or 'pool'}-{i}" if args.name else "", args.type)
            n += 1
        print(f"  ✓ added {n} pools from {args.file}")
    elif args.cmd == "list":
        pools = list_pools(DB)
        print(f"  {len(pools)} proxy pool(s):")
        for p in pools:
            print(f"    - {p['name']}  {p['proxyUrl']}  type={p['type']} active={p['isActive']} test={p['testStatus']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())