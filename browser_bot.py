#!/usr/bin/env python3
"""Browser automation for Cloudflare account processing"""

import json
import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional
from playwright.sync_api import sync_playwright, Browser, Page


class CFAutoGrabber:
    """Automated Cloudflare account grabber"""
    
    def __init__(self, email: str, password: str, headless: bool = True):
        self.email = email
        self.password = password
        self.headless = headless
        self.account_id = None
        self.api_token = None
        self.workers_ai_ok = False
        
    def login(self) -> bool:
        """Login to Cloudflare dashboard"""
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                context = browser.new_context()
                page = context.new_page()
                
                # Go to login page
                page.goto("https://dash.cloudflare.com/login")
                page.wait_for_load_state("networkidle")
                
                # Fill login form
                page.fill('input[name="email"]', self.email)
                page.fill('input[name="password"]', self.password)
                
                # Click login button
                page.click('button[type="submit"]')
                
                # Wait for dashboard
                page.wait_for_url("**/accounts**", timeout=30000)
                
                browser.close()
                return True
        except Exception as e:
            print(f"Login error: {e}")
            return False
    
    def get_account_id(self) -> bool:
        """Extract account ID from dashboard"""
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                context = browser.new_context()
                page = context.new_page()
                
                # Login
                page.goto("https://dash.cloudflare.com/login")
                page.wait_for_load_state("networkidle")
                page.fill('input[name="email"]', self.email)
                page.fill('input[name="password"]', self.password)
                page.click('button[type="submit"]')
                page.wait_for_url("**/accounts**", timeout=30000)
                
                # Extract account ID from URL
                url = page.url
                if "/accounts/" in url:
                    self.account_id = url.split("/accounts/")[1].split("/")[0]
                    browser.close()
                    return True
                
                browser.close()
                return False
        except Exception as e:
            print(f"Get account ID error: {e}")
            return False
    
    def create_workers_ai_token(self) -> bool:
        """Create Workers AI API token"""
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                context = browser.new_context()
                page = context.new_page()
                
                # Login
                page.goto("https://dash.cloudflare.com/login")
                page.wait_for_load_state("networkidle")
                page.fill('input[name="email"]', self.email)
                page.fill('input[name="password"]', self.password)
                page.click('button[type="submit"]')
                page.wait_for_url("**/accounts**", timeout=30000)
                
                # Navigate to API Tokens
                page.goto("https://dash.cloudflare.com/profile/api-tokens")
                page.wait_for_load_state("networkidle")
                
                # Create custom token
                page.click('text=Create Token')
                page.wait_for_timeout(2000)
                
                # Select custom token template
                page.click('text=Create Custom Token')
                page.wait_for_timeout(1000)
                
                # Fill token details
                token_name = f"WorkersAI-{self.email.split('@')[0]}-{int(time.time())}"
                page.fill('input[name="name"]', token_name)
                
                # Add Workers AI permission
                page.click('text=Add Permission')
                page.wait_for_timeout(1000)
                
                # Select Account
                page.select_option('select[name="accountId"]', index=1)
                
                # Select Workers AI
                page.select_option('select[name="serviceId"]', label="Workers AI")
                
                # Set access to Edit
                page.select_option('select[name="access"]', label="Edit")
                
                # Continue to summary
                page.click('text=Continue to summary')
                page.wait_for_timeout(2000)
                
                # Create token
                page.click('text=Create Token')
                page.wait_for_timeout(3000)
                
                # Extract token
                token_element = page.locator('input[name="token"]')
                if token_element.count() > 0:
                    self.api_token = token_element.first.input_value()
                    self.workers_ai_ok = True
                    browser.close()
                    return True
                
                browser.close()
                return False
        except Exception as e:
            print(f"Create token error: {e}")
            return False
    
    def export(self) -> Dict:
        """Export account data"""
        return {
            "email": self.email,
            "account_id": self.account_id,
            "api_token": self.api_token,
            "workers_ai_ok": self.workers_ai_ok
        }


def load_accounts(file_path: str) -> List[Dict[str, str]]:
    """Load accounts from JSON or TXT file"""
    path = Path(file_path)
    
    if not path.exists():
        print(f"Error: File {file_path} not found")
        sys.exit(1)
    
    accounts = []
    
    if path.suffix.lower() == '.json':
        # JSON format
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            if isinstance(data, list):
                for item in data:
                    if 'email' in item and 'password' in item:
                        accounts.append({
                            'email': item['email'],
                            'password': item['password']
                        })
    
    elif path.suffix.lower() == '.txt':
        # TXT format: email:password per line
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and ':' in line:
                    email, password = line.split(':', 1)
                    accounts.append({
                        'email': email.strip(),
                        'password': password.strip()
                    })
    
    else:
        print(f"Error: Unsupported file format. Use .json or .txt")
        sys.exit(1)
    
    if not accounts:
        print("Error: No valid accounts found in file")
        sys.exit(1)
    
    return accounts


def process_accounts(accounts: List[Dict[str, str]], headless: bool = True) -> List[Dict]:
    """Process multiple accounts"""
    results = []
    total = len(accounts)
    
    for idx, account in enumerate(accounts, 1):
        email = account['email']
        password = account['password']
        
        print(f"\n{'='*60}")
        print(f"Processing {idx}/{total}: {email}")
        print('='*60)
        
        grabber = CFAutoGrabber(email, password, headless)
        
        # Step 1: Login
        print(f"[1/4] Logging in...")
        if not grabber.login():
            print(f"❌ Login failed for {email}")
            results.append({
                'email': email,
                'status': 'login_failed'
            })
            continue
        print("✓ Login successful")
        
        # Step 2: Get Account ID
        print(f"[2/4] Getting Account ID...")
        if not grabber.get_account_id():
            print(f"❌ Failed to get Account ID for {email}")
            results.append({
                'email': email,
                'status': 'account_id_failed'
            })
            continue
        print(f"✓ Account ID: {grabber.account_id}")
        
        # Step 3: Create Token
        print(f"[3/4] Creating API token...")
        if not grabber.create_workers_ai_token():
            print(f"❌ Failed to create token for {email}")
            results.append({
                'email': email,
                'status': 'token_failed'
            })
            continue
        print("✓ Token created")
        
        # Step 4: Export
        print(f"[4/4] Exporting...")
        result = grabber.export()
        results.append(result)
        print("✓ Exported")
        
        print(f"\n✅ Success: {email}")
    
    # Save results
    output_dir = Path("exports")
    output_dir.mkdir(exist_ok=True)
    
    output_file = output_dir / "cf_accounts.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    
    print(f"\n{'='*60}")
    print(f"Results saved to: {output_file}")
    print(f"Total processed: {len(results)}/{total}")
    print('='*60)
    
    return results


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Cloudflare Account Automation")
    parser.add_argument("--accounts", required=True, help="Path to accounts file (JSON or TXT)")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    
    args = parser.parse_args()
    
    accounts = load_accounts(args.accounts)
    print(f"Loaded {len(accounts)} accounts from {args.accounts}")
    
    results = process_accounts(accounts, headless=args.headless)
