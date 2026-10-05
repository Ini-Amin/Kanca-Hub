#!/usr/bin/env python3
"""
Inject Grok/xAI SSO tokens (from grok-register) into 9Router via a grok2api bridge.

DISCOVERY (see --discover / printed on every run):
  9Router has a built-in provider id "xai", but it is an OAuth provider
  (authType "oauth", data = accessToken/refreshToken/idToken, scope
  "grok-cli:access api:access"). A grok.com SSO cookie token is NOT an OAuth token,
  so SSO tokens cannot be injected into provider "xai".
  The usable representation is an openai-compatible providerNode whose baseUrl is a
  grok2api server (it exposes /v1/chat/completions backed by SSO tokens), exactly like
  the TokenHarbor / Muse nodes:
      providerNodes:        type "openai-compatible",
                            data {"prefix","apiType":"chat","baseUrl":"<base>/v1"}
      providerConnections:  provider = <node id>, authType "apikey",
                            data {"apiKey": ..., "providerSpecificData": {...}}

Inputs (-i): grok2api pool token.json ({"ssoBasic":[{"token":..}]}) or an
accounts_*.txt file ('email----password----sso'), or a directory containing them.
Default: ~/grok-register (token.json + accounts_*.txt).

Modes:
  default     one connection per SSO token, apiKey = SSO token (for grok2api builds that
              accept the SSO token as Bearer and route to that account).
  --api-key K one shared connection with the grok2api client key K (env GROK2API_KEY);
              SSO tokens are only counted (they must live in the grok2api pool itself).

The bridge URL is required: --base-url or env GROK2API_BASE.

Usage:
  python3 grok_9router.py -i ~/grok-register/token.json --base-url http://127.0.0.1:8000 --dry-run
  python3 grok_9router.py -i ~/grok-register/accounts_x.txt --base-url http://127.0.0.1:8000 --verify
  python3 grok_9router.py --base-url http://127.0.0.1:8000 --api-key sk-xxx

Safe: timestamped DB backup before any write; --dry-run opens the DB read-only.
"""

from __future__ import annotations

import argparse
import glob
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

NODE_NAME = "grok2api"
NODE_PREFIX = "grok"
DEFAULT_MODEL = "grok-4"
DEFAULT_INPUT_DIR = Path.home() / "grok-register"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def mask(s: str, n: int = 6) -> str:
    return f"{s[:n]}…{s[-4:]}" if len(s) > n + 4 else "***"


# --------------------------------------------------------------------------- input
def clean_sso(raw: str) -> str:
    t = (raw or "").strip()
    if t.lower().startswith("sso="):
        t = t[4:]
    return t.split(";")[0].strip()


def parse_token_json(path: Path) -> list[dict]:
    data = json.loads(path.read_text())
    out: list[dict] = []
    pools = data if isinstance(data, dict) else {}
    for pool, items in pools.items():
        if not isinstance(items, list):
            continue
        for it in items:
            tok = clean_sso(it.get("token") if isinstance(it, dict) else str(it))
            if tok:
                note = it.get("note") if isinstance(it, dict) else None
                out.append({"token": tok, "email": note or None, "src": f"{path.name}:{pool}"})
    return out


def parse_accounts_txt(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "----" not in line:
            continue
        parts = line.split("----")
        if len(parts) < 3:
            continue
        tok = clean_sso(parts[-1])
        email = parts[0].strip()
        if tok:
            out.append({"token": tok, "email": email if "@" in email else None, "src": path.name})
    return out


def load_tokens(inp: Path) -> list[dict]:
    files: list[Path] = []
    if inp.is_dir():
        tj = inp / "token.json"
        if tj.exists():
            files.append(tj)
        files += sorted(Path(p) for p in glob.glob(str(inp / "accounts_*.txt")))
    elif inp.exists():
        files.append(inp)
    out: list[dict] = []
    seen: set[str] = set()
    for f in files:
        try:
            if f.suffix == ".json":
                rows = parse_token_json(f)
            else:
                rows = parse_accounts_txt(f)
        except Exception as e:  # noqa: BLE001
            print(f"  ! skip {f}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        for r in rows:
            if r["token"] not in seen:
                seen.add(r["token"])
                out.append(r)
    return out


# --------------------------------------------------------------------------- DB
def connect(db: Path, read_only: bool) -> sqlite3.Connection:
    if read_only:
        return sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    return sqlite3.connect(db)


def backup_db(db: Path) -> Path | None:
    if not db.exists():
        return None
    dest = db.with_suffix(f".sqlite.grok-{time.strftime('%Y%m%d-%H%M%S')}.bak")
    shutil.copy2(db, dest)
    return dest


def discover(con: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """Print how 9Router represents grok/xai; return matching nodes [(id,name,baseUrl)]."""
    print("== 9Router grok/xai discovery ==")
    found_nodes: list[tuple[str, str, str]] = []
    for nid, ntype, name, data in con.execute("SELECT id, type, name, data FROM providerNodes"):
        blob = f"{name} {data}".lower()
        if "grok" in blob or "xai" in blob or "x.ai" in blob:
            try:
                base = json.loads(data or "{}").get("baseUrl", "")
            except Exception:  # noqa: BLE001
                base = ""
            print(f"  node  id={nid} type={ntype} name={name} data={data}")
            found_nodes.append((nid, name, base))
    if not found_nodes:
        print("  nodes: no grok/xai providerNode")
    rows = con.execute(
        "SELECT id, provider, authType, name, email, priority, isActive, data "
        "FROM providerConnections WHERE lower(provider) LIKE '%xai%' OR lower(provider) LIKE '%grok%'"
    ).fetchall()
    for cid, prov, auth, name, email, prio, active, data in rows:
        try:
            d = json.loads(data or "{}")
        except Exception:  # noqa: BLE001
            d = {}
        shape = sorted(d.keys())
        print(f"  conn  id={cid[:8]}… provider={prov} authType={auth} name={name} "
              f"priority={prio} isActive={active}")
        print(f"        data keys={shape}  scope={d.get('scope')}")
    if rows and any(r[2] == "oauth" for r in rows):
        print("  => built-in 'xai' is OAuth (accessToken/refreshToken/idToken). "
              "SSO tokens cannot go there; using grok2api openai-compatible node instead.")
    elif not rows:
        print("  conns: none")
    return found_nodes


def ensure_node(con: sqlite3.Connection, base_v1: str, dry_run: bool) -> tuple[str, bool]:
    """Find a node by baseUrl, else create. Returns (node_id, created)."""
    for nid, ntype, data in con.execute("SELECT id, type, data FROM providerNodes"):
        try:
            if json.loads(data or "{}").get("baseUrl", "").rstrip("/") == base_v1.rstrip("/"):
                return nid, False
        except Exception:  # noqa: BLE001
            pass
    nid = f"openai-compatible-chat-{uuid.uuid4()}"
    ts = now_iso()
    node_data = {"prefix": NODE_PREFIX, "apiType": "chat", "baseUrl": base_v1}
    row = (nid, "openai-compatible", NODE_NAME, json.dumps(node_data, separators=(",", ":")), ts, ts)
    print(f"  + node  INSERT INTO providerNodes (id,type,name,data,createdAt,updatedAt) = {row[:4]}")
    if not dry_run:
        con.execute(
            "INSERT INTO providerNodes (id, type, name, data, createdAt, updatedAt) VALUES (?,?,?,?,?,?)", row
        )
    return nid, True


def upsert_connection(con: sqlite3.Connection, node_id: str, base_v1: str, key: str,
                      name: str, model: str, dry_run: bool) -> str:
    ts = now_iso()
    psd = {"prefix": NODE_PREFIX, "apiType": "chat", "baseUrl": base_v1, "nodeName": NODE_NAME,
           "connectionProxyEnabled": False, "connectionProxyUrl": "", "connectionNoProxy": ""}
    data = {"defaultModel": model, "apiKey": key, "testStatus": "active",
            "providerSpecificData": psd, "errorCode": None, "backoffLevel": 0,
            f"modelLock_{model}": None}

    existing = None
    for cid, cdata in con.execute("SELECT id, data FROM providerConnections WHERE provider = ?", (node_id,)):
        if key in (cdata or ""):
            existing = cid
            break
    shown = dict(data, apiKey=mask(key))
    if existing:
        print(f"  ~ update  {name}  id={existing[:8]}…  data={json.dumps(shown)}")
        if not dry_run:
            con.execute("UPDATE providerConnections SET data=?, isActive=1, name=?, updatedAt=? WHERE id=?",
                        (json.dumps(data), name, ts, existing))
        return existing
    new_id = str(uuid.uuid4())
    prio = (con.execute("SELECT COALESCE(MAX(priority),0) FROM providerConnections WHERE provider=?",
                        (node_id,)).fetchone()[0] or 0) + 1
    print(f"  + insert  {name}  (provider={node_id[:30]}…, authType=apikey, priority={prio}, isActive=1)")
    print(f"            data={json.dumps(shown)}")
    if not dry_run:
        con.execute(
            "INSERT INTO providerConnections (id, provider, authType, name, email, priority, isActive, data,"
            " createdAt, updatedAt) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (new_id, node_id, "apikey", name, None, prio, 1, json.dumps(data), ts, ts),
        )
    return new_id


# --------------------------------------------------------------------------- verify
def verify_key(base_v1: str, key: str, model: str, timeout: int = 60) -> tuple[bool, str]:
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "hi"}],
                       "max_tokens": 1}).encode()
    req = urllib.request.Request(f"{base_v1}/chat/completions", data=body, method="POST",
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status == 200, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return False, type(e).__name__


def normalize_base(url: str) -> str:
    u = url.strip().rstrip("/")
    return u if u.endswith("/v1") else u + "/v1"


def main() -> int:
    ap = argparse.ArgumentParser(description="Inject Grok SSO tokens into 9Router via grok2api bridge")
    ap.add_argument("-i", "--input", default=str(DEFAULT_INPUT_DIR),
                    help="token.json, accounts_*.txt, or a directory containing them "
                         f"(default: {DEFAULT_INPUT_DIR})")
    ap.add_argument("--db", default=str(DB_PATH), help=f"9Router DB (default: {DB_PATH})")
    ap.add_argument("--base-url", default=os.environ.get("GROK2API_BASE", ""),
                    help="grok2api base URL (env GROK2API_BASE); '/v1' appended if missing")
    ap.add_argument("--api-key", default=os.environ.get("GROK2API_KEY", ""),
                    help="shared grok2api client key; creates ONE connection instead of per-token ones")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"default model (default: {DEFAULT_MODEL})")
    ap.add_argument("--verify", action="store_true", help="test each key against the bridge first")
    ap.add_argument("--dry-run", action="store_true", help="show planned rows, write nothing")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"✗ 9Router DB not found: {db}", file=sys.stderr)
        return 1

    con = connect(db, read_only=True)
    try:
        discover(con)
    finally:
        con.close()

    if not args.base_url:
        print("\n✗ A grok2api bridge URL is required (9Router's built-in 'xai' provider is OAuth-only, "
              "so SSO tokens need grok2api).\n  Pass --base-url http://HOST:PORT or set GROK2API_BASE.",
              file=sys.stderr)
        return 2
    base_v1 = normalize_base(args.base_url)

    inp = Path(args.input).expanduser()
    if not inp.exists():
        print(f"✗ input not found: {inp}", file=sys.stderr)
        return 1
    tokens = load_tokens(inp)
    print(f"\nLoaded {len(tokens)} unique SSO token(s) from {inp}.")

    shared = bool(args.api_key)
    if shared:
        items = [{"token": args.api_key, "email": None, "label": f"{NODE_NAME}-shared"}]
        print(f"Mode: shared grok2api key (1 connection). {len(tokens)} SSO token(s) must live in the "
              "grok2api pool itself.")
    else:
        if not tokens:
            print("Nothing to inject.")
            return 0
        items = [dict(t, label=t["email"] or f"grok-{t['token'][-6:]}") for t in tokens]
        print("Mode: per-token connections (apiKey = SSO token).")

    if args.verify:
        print(f"Verifying against {base_v1} …")
        kept = []
        for it in items:
            ok, detail = verify_key(base_v1, it["token"], args.model)
            print(f"  {'✅' if ok else '❌'} {it['label']:34s} {detail}")
            if ok:
                kept.append(it)
        items = kept
        if not items:
            print("No keys passed verification.")
            return 1

    if args.dry_run:
        print("\nDRY RUN — no changes will be written.")
    else:
        b = backup_db(db)
        if b:
            print(f"Backup: {b}")

    con = connect(db, read_only=args.dry_run)
    try:
        if not args.dry_run:
            con.execute("PRAGMA journal_mode=WAL")
        node_id, created = ensure_node(con, base_v1, args.dry_run)
        if not created:
            print(f"  = node exists: {node_id}")
        for it in items:
            upsert_connection(con, node_id, base_v1, it["token"], it["label"], args.model, args.dry_run)
        if not args.dry_run:
            con.commit()
    finally:
        con.close()

    verb = "would inject" if args.dry_run else "Injected"
    print(f"\n{verb} {len(items)} connection(s) into 9Router (node {NODE_NAME}, base {base_v1}).")
    if not args.dry_run:
        print("9Router reads the DB live. If the provider doesn't show up, restart it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
