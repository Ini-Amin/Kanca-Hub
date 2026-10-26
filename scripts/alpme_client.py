#!/usr/bin/env python3
"""scripts/alpme_client.py — Standalone Key Issuer and Gateway Client for Alpme.com.

Reverse-engineered from alpme-1.0.25.vsix:
  1. POST https://alpme.com/api/vscode/auth/link/start -> Device link code
  2. POST https://alpme.com/api/auth/login             -> Web session cookie (ai_sess)
  3. POST https://alpme.com/api/link/approve           -> Approve link without VS Code
  4. GET  https://alpme.com/api/vscode/auth/link/{code}-> Exchange approved link for account_token
  5. POST https://alpme.com/api/vscode/keys           -> Mint permanent Gateway API key (sk_live_...)

Usage:
  python3 scripts/alpme_client.py mint --email <EMAIL> --password <PASS> [--name "Agent Key"]
  python3 scripts/alpme_client.py list --email <EMAIL> --password <PASS>
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "https://alpme.com"
CLIENT_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "x-alpme-client": "vscode",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Code/1.96.0",
}


def login(email: str, password: str) -> str:
    """Log in to Alpme web account and return ai_sess cookie value."""
    url = f"{BASE_URL}/api/auth/login"
    payload = json.dumps({"email": email, "password": password}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "Mozilla/5.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=12) as r:
        cookies = r.headers.get("Set-Cookie", "")
        for part in cookies.split(";"):
            part = part.strip()
            if part.startswith("ai_sess="):
                return part.split("=", 1)[1]
    raise RuntimeError("Failed to obtain ai_sess session cookie")


def start_link() -> tuple[str, str]:
    """Start device link flow; returns (code, user_code)."""
    url = f"{BASE_URL}/api/vscode/auth/link/start"
    req = urllib.request.Request(url, data=b"{}", headers=CLIENT_HEADERS, method="POST")
    with urllib.request.urlopen(req, timeout=12) as r:
        data = json.loads(r.read())
        return data["code"], data["userCode"]


def approve_link(ai_sess: str, user_code: str) -> bool:
    """Approve the pending userCode on the web session."""
    url = f"{BASE_URL}/api/link/approve"
    payload = json.dumps({"code": user_code}).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Cookie": f"ai_sess={ai_sess}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=12) as r:
        res = json.loads(r.read())
        return bool(res.get("ok"))


def poll_link(code: str, retries: int = 10, interval: float = 1.5) -> str:
    """Poll for approved account token."""
    url = f"{BASE_URL}/api/vscode/auth/link/{code}"
    headers = dict(CLIENT_HEADERS)
    headers.pop("Content-Type", None)

    for _ in range(retries):
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=12) as r:
            data = json.loads(r.read())
            if data.get("status") == "approved" and data.get("token"):
                return data["token"]
        time.sleep(interval)
    raise RuntimeError("Timed out waiting for device link approval")


def mint_api_key(account_token: str, name: str = "Agent Gateway Key") -> dict:
    """Mint a fresh Gateway API key via /api/vscode/keys."""
    url = f"{BASE_URL}/api/vscode/keys"
    headers = dict(CLIENT_HEADERS)
    headers["Authorization"] = f"Bearer {account_token}"
    payload = json.dumps({"name": name}).encode()

    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read())


def list_keys(ai_sess: str) -> dict:
    url = f"{BASE_URL}/api/keys"
    req = urllib.request.Request(
        url,
        headers={"Cookie": f"ai_sess={ai_sess}", "Accept": "application/json", "User-Agent": "Mozilla/5.0"},
    )
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser(description="Alpme.com Headless Key Minting & Gateway Client")
    sub = ap.add_subparsers(dest="cmd", required=True)

    mint_p = sub.add_parser("mint", help="Mint a fresh Gateway API key")
    mint_p.add_argument("--email", required=True, help="Alpme account email")
    mint_p.add_argument("--password", required=True, help="Alpme account password")
    mint_p.add_argument("--name", default="Agent Key", help="Display name for key")

    list_p = sub.add_parser("list", help="List active keys from web dashboard")
    list_p.add_argument("--email", required=True, help="Alpme account email")
    list_p.add_argument("--password", required=True, help="Alpme account password")

    args = ap.parse_args()

    if args.cmd == "mint":
        print(f"[*] Authenticating as {args.email}...")
        sess = login(args.email, args.password)
        print("[*] Starting device link flow...")
        code, user_code = start_link()
        print(f"[*] Approving userCode: {user_code}...")
        if not approve_link(sess, user_code):
            sys.exit("[-] Approval rejected by server.")
        print("[*] Exchanging link token...")
        token = poll_link(code)
        print(f"[*] Minting Gateway key '{args.name}'...")
        res = mint_api_key(token, args.name)
        secret = res.get("secret")
        print("\n================== KEY GENERATED ==================")
        print(f"API KEY : {secret}")
        print(f"KEY ID  : {res.get('key', {}).get('id')}")
        print(f"PLAN    : {res.get('quota', {}).get('plan', {}).get('name')}")
        print(f"ACTIVE  : {res.get('quota', {}).get('active')}/{res.get('quota', {}).get('max')}")
        print("===================================================\n")
    elif args.cmd == "list":
        sess = login(args.email, args.password)
        data = list_keys(sess)
        keys = data.get("keys", [])
        print(f"Active Keys ({len(keys)}):")
        for k in keys:
            print(f"  [{k.get('id')}] {k.get('name'):25s} {k.get('masked')} ({k.get('status')})")


if __name__ == "__main__":
    main()
