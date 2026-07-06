#!/usr/bin/env python3
"""
CF Cookie Processor — terima cookie JSON dari Cookie-Editor → inject → extract token + info
Usage: venv/bin/python3 process_cookies.py <cookies.json> <account_label>
"""

import json, sys, os
from curl_cffi import requests
from pathlib import Path

ACCOUNTS_FILE = Path(__file__).parent / "accounts.json"

def load_cookies(cookie_file: str) -> dict:
    """Load Cookie-Editor JSON → dict untuk curl_cffi"""
    with open(cookie_file) as f:
        raw = json.load(f)
    
    # Cookie-Editor format: [{name, value, domain, ...}]
    # curl_cffi format: {domain: {name: value, ...}}
    cookies = {}
    for c in raw:
        domain = c.get("domain", ".cloudflare.com")
        if domain not in cookies:
            cookies[domain] = {}
        cookies[domain][c["name"]] = c["value"]
    
    return cookies

def verify_session(session, cookies: dict) -> dict | None:
    """Hitung API endpoint untuk verifikasi login → return account info"""
    r = session.get(
        "https://api.cloudflare.com/client/v4/user/tokens",
        cookies=cookies,
        impersonate="chrome120",
    )
    if r.status_code == 200 and r.json().get("success"):
        print("✅ Login valid!")
        return r.json()
    
    print(f"❌ Login failed: {r.status_code} - {r.text[:200]}")
    return None

def get_accounts(session, cookies: dict) -> list:
    """Get list of accounts"""
    r = session.get(
        "https://api.cloudflare.com/client/v4/accounts",
        cookies=cookies,
        impersonate="chrome120",
    )
    data = r.json()
    if data.get("success"):
        return data.get("result", [])
    return []

def create_token(session, cookies: dict, account_id: str | None = None) -> str | None:
    """Create API token dengan full permissions"""
    # Default: all permissions on all zones
    policies = [
        {
            "effect": "allow",
            "resources": {"com.cloudflare.api.account.*": "*"},
            "permission_groups": [
                {"id": "c8fed203ed3043cba015a93ad1616f1f"},  # Account Settings:Read
                {"id": "82e64a83756745bbbb1c9c2701bf816b"},  # Account Settings:Edit
                {"id": "8d67c40ef2de4f35a7708ab1590426b9"},  # Workers Scripts:Edit
                {"id": "e17beae8b4cb423a99b14d212ef7b3b5"},  # Workers KV Storage:Edit
                {"id": "15a5824253ea472ca748634e0e7a0962"},  # AI:Edit
            ],
        }
    ]
    
    payload = {
        "name": "auto-generated-token",
        "policies": policies,
    }
    
    r = session.post(
        "https://api.cloudflare.com/client/v4/user/tokens",
        cookies=cookies,
        json=payload,
        impersonate="chrome120",
    )
    data = r.json()
    if data.get("success"):
        token = data["result"]["value"]
        print(f"✅ Token created: {token[:20]}...")
        return token
    
    # Fallback: maybe simpler token
    print(f"⚠️  Policy token failed: {data.get('errors', [])}")
    
    # Try simple template token
    r2 = session.post(
        "https://api.cloudflare.com/client/v4/user/tokens",
        cookies=cookies,
        json={
            "name": "auto-token-simple",
            "status": "active",
        },
        impersonate="chrome120",
    )
    d2 = r2.json()
    if d2.get("success"):
        token = d2["result"]["value"]
        print(f"✅ Simple token created: {token[:20]}...")
        return token
    
    print(f"❌ Token creation failed: {d2.get('errors', [])}")
    return None

def get_workers_ai_info(session, cookies: dict, account_id: str) -> dict:
    """Get Workers AI subscription info"""
    # Try to check AI gateway
    r = session.get(
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai-gateway/gateways",
        cookies=cookies,
        impersonate="chrome120",
    )
    gateways = r.json()
    
    # Check account entitlements
    r2 = session.get(
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/intel/entitlements",
        cookies=cookies,
        impersonate="chrome120",
    )
    entitlements = r2.json()
    
    # Check Workers subscription
    r3 = session.get(
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/workers/subscription",
        cookies=cookies,
        impersonate="chrome120",
    )
    sub = r3.json()
    
    return {
        "gateways": gateways.get("result", []),
        "entitlements": entitlements.get("result", []),
        "workers_sub": sub.get("result", {}),
    }

def save_account(email: str, label: str, data: dict):
    """Save to accounts.json"""
    if ACCOUNTS_FILE.exists():
        with open(ACCOUNTS_FILE) as f:
            try:
                accounts = json.load(f)
            except:
                accounts = []
    else:
        accounts = []
    
    accounts.append(data)
    
    with open(ACCOUNTS_FILE, "w") as f:
        json.dump(accounts, f, indent=2)
    
    print(f"📁 Saved to {ACCOUNTS_FILE} ({len(accounts)} total)")

def main():
    if len(sys.argv) < 3:
        print("Usage: process_cookies.py <cookies.json> <account_label>")
        print("  account_label: e.g., azisjati92, akun-1, dll")
        sys.exit(1)
    
    cookie_file = sys.argv[1]
    label = sys.argv[2]
    
    print(f"🔐 Loading cookies from {cookie_file}...")
    cookies = load_cookies(cookie_file)
    print(f"   Got {sum(len(v) for v in cookies.values())} cookies across {len(cookies)} domains")
    
    session = requests.Session()
    
    # Step 1: Verify login
    print("\n🔍 Verifying session...")
    token_data = verify_session(session, cookies)
    if not token_data:
        sys.exit(1)
    
    # Step 2: Get accounts
    print("\n📋 Fetching accounts...")
    accounts = get_accounts(session, cookies)
    print(f"   Found {len(accounts)} account(s):")
    for acc in accounts:
        print(f"   - {acc['name']} ({acc['id']})")
    
    if not accounts:
        print("❌ No accounts found — session may be incomplete")
        sys.exit(1)
    
    account_id = accounts[0]["id"]
    account_name = accounts[0]["name"]
    
    # Step 3: Create API token
    print("\n🔑 Creating API token...")
    token = create_token(session, cookies, account_id)
    
    # Step 4: Get Workers AI info
    print("\n🤖 Fetching Workers AI info...")
    ai_info = get_workers_ai_info(session, cookies, account_id)
    
    # Step 5: Save
    result = {
        "label": label,
        "account_name": account_name,
        "account_id": account_id,
        "api_token": token,
        "workers_ai": ai_info,
        "cookies_file": os.path.basename(cookie_file),
    }
    
    save_account(account_name, label, result)
    
    print(f"\n{'='*50}")
    print(f"✅ DONE — {label}")
    print(f"   Account: {account_name}")
    print(f"   Token: {'✅' if token else '❌'}")
    print(f"   AI Gates: {len(ai_info['gateways'])}")
    print(f"{'='*50}")

if __name__ == "__main__":
    main()
