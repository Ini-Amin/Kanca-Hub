#!/usr/bin/env python3
"""
Auto-FreeCF: Cloudflare Workers AI Token Auto-Grabber

Auto-login to Cloudflare account and grab Workers AI API token + Account ID.
No browser automation - pure HTTP with session management.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

import httpx
from curl_cffi import requests as cffi_requests


CF_API = "https://api.cloudflare.com/client/v4"
CF_DASH = "https://dash.cloudflare.com"
EXPORT_DIR = Path("/root/cf-account-bot/exports")


class CFAutoGrabber:
    """Auto-login to CF and grab Workers AI token"""
    
    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.session = cffi_requests.Session(impersonate="chrome120")
        self.cookies = {}
        self.account_id = None
        self.api_token = None
    
    def login(self) -> bool:
        """Login to Cloudflare dashboard"""
        print(f"🔐 Logging in as {self.email}...")
        
        # CF login endpoint
        login_url = f"{CF_DASH}/api/v4/user"
        
        try:
            resp = self.session.post(
                login_url,
                json={
                    "email": self.email,
                    "password": self.password
                }
            )
            
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    print("✅ Login successful")
                    self.cookies = dict(resp.cookies)
                    return True
            
            print(f"❌ Login failed: {resp.text[:200]}")
            return False
            
        except Exception as e:
            print(f"❌ Login error: {e}")
            return False
    
    def get_account_id(self) -> Optional[str]:
        """Get account ID from session"""
        print("🔍 Fetching account ID...")
        
        try:
            resp = self.session.get(
                f"{CF_API}/accounts",
                cookies=self.cookies
            )
            
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success") and data.get("result"):
                    account = data["result"][0]
                    self.account_id = account["id"]
                    print(f"✅ Account ID: {self.account_id}")
                    return self.account_id
            
            print(f"❌ Failed to get account ID: {resp.text[:200]}")
            return None
            
        except Exception as e:
            print(f"❌ Error fetching account ID: {e}")
            return None
    
    def create_custom_api_token(self) -> Optional[str]:
        """Create Custom API token (HTTP-based, for API access)"""
        print("🔑 Creating Workers AI API token...")
        
        if not self.account_id:
            print("❌ No account ID available")
            return None
        
        token_url = f"{CF_API}/user/tokens"
        
        # Workers AI read permission
        payload = {
            "name": f"Workers AI Bot - {int(time.time())}",
            "policies": [{
                "effect": "allow",
                "resources": {
                    f"com.cloudflare.api.account.{self.account_id}": "*"
                },
                "permission_groups": [{
                    "id": "c8fed203ed3043cba015a93ad1616f1f",  # Workers AI Read
                    "name": "Workers AI Read"
                }]
            }]
        }
        
        try:
            resp = self.session.post(
                token_url,
                json=payload,
                cookies=self.cookies
            )
            
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    self.api_token = data["result"]["value"]
                    print(f"✅ API Token created: {self.api_token[:20]}...")
                    return self.api_token
            
            print(f"❌ Failed to create token: {resp.text[:200]}")
            return None
            
        except Exception as e:
            print(f"❌ Error creating token: {e}")
            return None
    
    def test_workers_ai(self) -> bool:
        """Test Workers AI access"""
        print("🧪 Testing Workers AI access...")
        
        if not self.api_token or not self.account_id:
            print("❌ Missing token or account ID")
            return False
        
        test_url = f"{CF_API}/accounts/{self.account_id}/ai/run/@cf/meta/llama-3.1-8b-instruct"
        
        payload = {
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 10
        }
        
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }
        
        try:
            resp = httpx.post(test_url, json=payload, headers=headers, timeout=30)
            
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success"):
                    print("✅ Workers AI access verified")
                    return True
            
            print(f"❌ Workers AI test failed: {resp.text[:200]}")
            return False
            
        except Exception as e:
            print(f"❌ Error testing Workers AI: {e}")
            return False
    
    def export(self) -> dict:
        """Export credentials"""
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        
        result = {
            "email": self.email,
            "password": self.password,
            "account_id": self.account_id,
            "api_token": self.api_token,
            "workers_ai_ok": self.test_workers_ai() if self.api_token else False,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        # Save to JSON
        json_file = EXPORT_DIR / "cf_accounts.json"
        accounts = []
        if json_file.exists():
            accounts = json.loads(json_file.read_text())
        
        accounts.append(result)
        json_file.write_text(json.dumps(accounts, indent=2))
        
        print(f"\n✅ Exported to {json_file}")
        print(f"   Email: {result['email']}")
        print(f"   Account ID: {result['account_id']}")
        print(f"   API Token: {result['api_token'][:20] if result['api_token'] else 'N/A'}...")
        
        return result


def main():
    parser = argparse.ArgumentParser(description="Auto-FreeCF: CF Workers AI Token Grabber")
    parser.add_argument("--email", required=True, help="Cloudflare account email")
    parser.add_argument("--password", required=True, help="Cloudflare account password")
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("Auto-FreeCF: Cloudflare Workers AI Token Auto-Grabber")
    print("=" * 60)
    
    grabber = CFAutoGrabber(args.email, args.password)
    
    # Step 1: Login
    if not grabber.login():
        sys.exit(1)
    
    # Step 2: Get Account ID
    if not grabber.get_account_id():
        sys.exit(1)
    
    # Step 3: Create Workers AI Token
    if not grabber.create_custom_api_token():
        sys.exit(1)
    
    # Step 4: Export
    grabber.export()
    
    print("\n" + "=" * 60)
    print("✅ SUCCESS: Token grabbed and exported!")
    print("=" * 60)


if __name__ == "__main__":
    main()
