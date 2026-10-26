#!/usr/bin/env python3
"""scripts/alpme_farm.py — Automated Alpme Account Farm & 9Router Injection.

Features:
  1. Registers fresh accounts on https://alpme.com/signup via clean proxy relay.
  2. Auto-redeems promo code (e.g. ALP27) for 180-day Ultra plan.
  3. Mints gateway API keys via device-link flow.
  4. Stores full credentials in ~/.config/auto-freecf/alpme_accounts.json.
  5. Injects accounts into 9Router database (~/.9router/db/data.sqlite) for
     multi-account load balancing and automatic 503 fallback.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# Local imports
import scripts.persona as persona
import scripts.alpme_client as ac

LEDGER_PATH = Path.home() / ".config/auto-freecf/alpme_accounts.json"
NINE_ROUTER_DB = Path(os.environ.get("NINE_ROUTER_DB", Path.home() / ".9router/db/data.sqlite"))
DEFAULT_PROXY = os.environ.get("ALPME_PROXY", "http://127.0.0.1:25346")
PROMO_CODE = "ALP27"
BASE_URL = "https://alpme.com"


def register_account(email: str, password: str, name: str, proxy_url: str | None = DEFAULT_PROXY) -> str:
    """Register account on Alpme and return session cookie ai_sess."""
    import requests
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    payload = {
        "email": email,
        "password": password,
        "name": name,
        "ref": "",
        "promo": "",
    }
    r = requests.post(
        f"{BASE_URL}/api/auth/signup",
        json=payload,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        },
        proxies=proxies,
        timeout=25,
    )
    if not r.ok:
        raise RuntimeError(f"Signup HTTP {r.status_code}: {r.text}")
    sess = r.cookies.get("ai_sess")
    if not sess:
        for header in r.headers.get("set-cookie", "").split(","):
            if "ai_sess=" in header:
                sess = header.split("ai_sess=")[1].split(";")[0]
                break
    if not sess:
        raise RuntimeError(f"Signup succeeded but ai_sess cookie was not found in response: {r.headers}")
    return sess


def redeem_promo(ai_sess: str, code: str = PROMO_CODE, proxy_url: str | None = DEFAULT_PROXY) -> dict:
    """Redeem promo code to upgrade plan (e.g. ALP27 -> Ultra 180 days)."""
    import requests
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
    r = requests.post(
        f"{BASE_URL}/api/promo/redeem",
        json={"code": code},
        headers={
            "Cookie": f"ai_sess={ai_sess}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        },
        proxies=proxies,
        timeout=25,
    )
    if not r.ok:
        raise RuntimeError(f"Promo redeem HTTP {r.status_code}: {r.text}")
    return r.json()

def mint_account_key(ai_sess: str, key_name: str) -> str:
    """Start device link, approve on session, poll token, and mint key."""
    code, user_code = ac.start_link()
    ac.approve_link(ai_sess, user_code)
    account_token = ac.poll_link(code)
    res = ac.mint_api_key(account_token, key_name)
    secret = res.get("secret")
    if not secret:
        raise RuntimeError(f"Minting key failed, response did not contain secret: {res}")
    return secret


def load_ledger() -> list[dict]:
    if not LEDGER_PATH.exists():
        return []
    try:
        return json.loads(LEDGER_PATH.read_text())
    except Exception:
        return []


def save_ledger(accounts: list[dict]) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.write_text(json.dumps(accounts, indent=2))


def inject_into_9router(account: dict) -> None:
    """Inject minted Alpme account key as an independent connection in 9Router DB."""
    if not NINE_ROUTER_DB.exists():
        print(f"Warning: 9Router DB not found at {NINE_ROUTER_DB}")
        return

    con = sqlite3.connect(NINE_ROUTER_DB)
    cur = con.cursor()

    node_id = "openai-compatible-chat-alpme"
    conn_id = f"alpme-ultra-{account['username']}"
    conn_name = f"Alpme Ultra ({account['username']})"
    now_iso = datetime.now(timezone.utc).isoformat()

    conn_data = {
        "apiKey": account["api_key"],
        "defaultModel": "claude-opus-5-5",
        "testStatus": "active",
        "providerSpecificData": {
            "authMethod": "api_key",
            "connectionProxyEnabled": False,
            "connectionProxyUrl": "",
            "connectionNoProxy": "",
            "baseUrl": "https://alpme.com/v1",
        },
    }

    # Upsert providerConnections
    cur.execute(
        """
        INSERT INTO providerConnections (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt)
        VALUES (?, ?, 'api_key', ?, ?, 0, 1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name = excluded.name,
            data = excluded.data,
            updatedAt = excluded.updatedAt
        """,
        (conn_id, node_id, conn_name, account["email"], json.dumps(conn_data), now_iso, now_iso),
    )
    con.commit()
    print(f"  [9Router] Injected connection '{conn_name}' (ID: {conn_id})")


def create_one_account(promo: str = PROMO_CODE, proxy_url: str | None = DEFAULT_PROXY) -> dict:
    name = persona.full_name()
    username = persona.username()
    email = f"{username}@kancalabs.my.id"
    password = persona.password()

    print(f"\n[+] Registering {name} <{email}>...")
    sess = register_account(email, password, name, proxy_url=proxy_url)
    print("  [✓] Signup successful, session cookie obtained.")

    print(f"  [+] Redeeming promo '{promo}'...")
    p_res = redeem_promo(sess, code=promo, proxy_url=proxy_url)
    msg = p_res.get("message", "Promo applied")
    print(f"  [✓] {msg}")

    print("  [+] Minting Gateway API Key via device-link flow...")
    key_name = f"Ultra Agent Key ({username})"
    secret_key = mint_account_key(sess, key_name)
    print(f"  [✓] API Key minted: {secret_key[:14]}... (Ultra Plan Active)")

    acc_entry = {
        "email": email,
        "username": username,
        "password": password,
        "name": name,
        "session_cookie": sess,
        "api_key": secret_key,
        "plan": "ultra",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Save to ledger
    ledger = load_ledger()
    ledger.append(acc_entry)
    save_ledger(ledger)

    # Inject into 9Router
    inject_into_9router(acc_entry)
    return acc_entry


def main():
    parser = argparse.ArgumentParser(description="Alpme Account Farm & 9Router Injection")
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    create_p = subparsers.add_parser("create", help="Create N new Alpme Ultra accounts")
    create_p.add_argument("-n", "--count", type=int, default=1, help="Number of accounts to create (default: 1)")
    create_p.add_argument("--promo", default=PROMO_CODE, help=f"Promo code (default: {PROMO_CODE})")
    create_p.add_argument("--proxy", default=DEFAULT_PROXY, help=f"Proxy URL (default: {DEFAULT_PROXY})")

    subparsers.add_parser("list", help="List all farmed Alpme accounts")

    inject_p = subparsers.add_parser("sync", help="Sync all existing accounts into 9Router DB")

    args = parser.parse_args()

    if args.cmd == "create":
        print(f"[*] Starting creation of {args.count} Alpme Ultra account(s)...")
        created = []
        for i in range(args.count):
            print(f"\n--- Account {i+1} of {args.count} ---")
            try:
                acc = create_one_account(promo=args.promo, proxy_url=args.proxy)
                created.append(acc)
                if i + 1 < args.count:
                    time.sleep(3)
            except Exception as e:
                print(f"  [!] Failed to create account {i+1}: {e}")
        print(f"\n[✓] Done! Successfully created and injected {len(created)} account(s).")

    elif args.cmd == "list":
        ledger = load_ledger()
        print(f"Total farmed Alpme accounts: {len(ledger)}")
        for i, a in enumerate(ledger, 1):
            print(f"{i:2d}. {a.get('email'):35s} | Plan: {a.get('plan'):6s} | Key: {a.get('api_key')[:18]}... | {a.get('created_at')[:10]}")

    elif args.cmd == "sync":
        ledger = load_ledger()
        print(f"Syncing {len(ledger)} accounts to 9Router DB...")
        for a in ledger:
            inject_into_9router(a)
        print("[✓] All accounts synchronized into 9Router.")


if __name__ == "__main__":
    main()
