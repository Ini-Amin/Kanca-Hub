#!/usr/bin/env python3
"""Browser automation for Cloudflare account processing"""

import json
import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page


class CFAutoGrabber:
    """Automated Cloudflare account grabber - uses single browser session"""
    
    def __init__(self, email: str, password: str, headless: bool = True):
        self.email = email
        self.password = password
        self.headless = headless
        self.account_id = None
        self.api_token = None
        self.workers_ai_ok = False
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
    
    def _start_browser(self):
        """Start browser session"""
        if self._browser is None:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=self.headless)
            self._context = self._browser.new_context()
            self._page = self._context.new_page()
    
    def _close_browser(self):
        """Close browser session"""
        if self._browser:
            try:
                self._browser.close()
            except:
                pass
            self._browser = None
        if self._playwright:
            try:
                self._playwright.stop()
            except:
                pass
            self._playwright = None
        self._context = None
        self._page = None
    
    def login(self) -> bool:
        """Login to Cloudflare dashboard"""
        try:
            self._start_browser()
            page = self._page
            
            # Go to login page
            print(f"  → Opening Cloudflare login...")
            page.goto("https://dash.cloudflare.com/login", wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            
            # Fill login form
            print(f"  → Filling credentials...")
            page.fill('input[name="email"]', self.email)
            page.fill('input[name="password"]', self.password)
            
            # Click login button
            print(f"  → Submitting login...")
            page.click('button[type="submit"]')
            
            # Wait for redirect - Cloudflare redirects to dashboard after login
            # URL pattern: https://dash.cloudflare.com/{account_id} or /home
            page.wait_for_timeout(5000)
            
            current_url = page.url
            print(f"  → Current URL: {current_url}")
            
            # Check if we're logged in (not on login page anymore)
            if "/login" in current_url:
                print(f"  ❌ Still on login page - credentials may be wrong")
                return False
            
            # Try to extract account ID from URL
            if "/home" in current_url:
                # Sometimes redirects to /home, need to navigate to get account ID
                print(f"  → Redirected to /home, navigating to get account ID...")
                page.goto("https://dash.cloudflare.com/", wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                current_url = page.url
            
            # Extract account ID from URL
            # Pattern: https://dash.cloudflare.com/{account_id}
            parts = current_url.split("dash.cloudflare.com/")
            if len(parts) > 1:
                account_part = parts[1].split("/")[0].split("?")[0]
                if account_part and account_part not in ["login", "home", "sign-up", ""]:
                    self.account_id = account_part
                    print(f"  ✓ Account ID: {self.account_id}")
                    return True
            
            # If we can't get account ID from URL, try to find it in the page
            print(f"  → Trying to find account ID in page...")
            page.wait_for_timeout(2000)
            
            # Look for account ID in page content or links
            links = page.query_selector_all('a[href*="/"]')
            for link in links:
                href = link.get_attribute("href") or ""
                if "dash.cloudflare.com/" in href:
                    link_parts = href.split("dash.cloudflare.com/")
                    if len(link_parts) > 1:
                        potential_id = link_parts[1].split("/")[0].split("?")[0]
                        if potential_id and len(potential_id) > 20 and potential_id.isdigit():
                            self.account_id = potential_id
                            print(f"  ✓ Account ID found: {self.account_id}")
                            return True
            
            print(f"  ❌ Could not extract account ID")
            return False
            
        except Exception as e:
            print(f"  ❌ Login error: {e}")
            return False
    
    def get_account_id(self) -> bool:
        """Get account ID (already done during login)"""
        if self.account_id:
            return True
        
        # If login didn't get account ID, try to get it now
        try:
            page = self._page
            if page is None:
                return False
            
            current_url = page.url
            print(f"  → Current URL: {current_url}")
            
            # Extract from URL
            parts = current_url.split("dash.cloudflare.com/")
            if len(parts) > 1:
                account_part = parts[1].split("/")[0].split("?")[0]
                if account_part and account_part not in ["login", "home", "sign-up", "profile", ""]:
                    self.account_id = account_part
                    return True
            
            # Navigate to main page to find account ID
            page.goto("https://dash.cloudflare.com/", wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            
            current_url = page.url
            parts = current_url.split("dash.cloudflare.com/")
            if len(parts) > 1:
                account_part = parts[1].split("/")[0].split("?")[0]
                if account_part and account_part not in ["login", "home", "sign-up", "profile", ""]:
                    self.account_id = account_part
                    return True
            
            return False
            
        except Exception as e:
            print(f"  ❌ Get account ID error: {e}")
            return False
    
    def create_workers_ai_token(self) -> bool:
        """Create Workers AI API token"""
        try:
            page = self._page
            if page is None:
                print(f"  ❌ Browser not started")
                return False
            
            if not self.account_id:
                print(f"  ❌ No account ID available")
                return False
            
            # Navigate to API Tokens page
            print(f"  → Navigating to API Tokens page...")
            page.goto("https://dash.cloudflare.com/profile/api-tokens", wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            
            # Click "Create Token" button
            print(f"  → Clicking Create Token...")
            create_btn = page.query_selector('a:has-text("Create Token"), button:has-text("Create Token")')
            if create_btn:
                create_btn.click()
                page.wait_for_timeout(2000)
            else:
                # Try alternative selectors
                page.click('text=Create Token')
                page.wait_for_timeout(2000)
            
            # Click "Create Custom Token"
            print(f"  → Selecting Custom Token...")
            custom_btn = page.query_selector('a:has-text("Create Custom Token"), button:has-text("Create Custom Token")')
            if custom_btn:
                custom_btn.click()
                page.wait_for_timeout(2000)
            else:
                page.click('text=Create Custom Token')
                page.wait_for_timeout(2000)
            
            # Fill token name
            token_name = f"WorkersAI-{self.email.split('@')[0]}-{int(time.time())}"
            print(f"  → Token name: {token_name}")
            
            name_input = page.query_selector('input[name="name"]')
            if name_input:
                name_input.fill(token_name)
            else:
                # Try alternative
                page.fill('input[placeholder*="name"]', token_name)
            
            page.wait_for_timeout(1000)
            
            # Add permission - Account > Workers AI > Edit
            print(f"  → Adding Workers AI permission...")
            
            # Click "Add Permission" or similar button
            add_perm_btn = page.query_selector('button:has-text("Add Permission"), a:has-text("Add Permission")')
            if add_perm_btn:
                add_perm_btn.click()
                page.wait_for_timeout(1000)
            
            # Select Account resource type
            print(f"  → Selecting Account resource...")
            resource_select = page.query_selector('select[name*="resource"], select[aria-label*="Resource"]')
            if resource_select:
                resource_select.select_option(label="Account")
                page.wait_for_timeout(500)
            
            # Select Workers AI service
            print(f"  → Selecting Workers AI service...")
            service_select = page.query_selector('select[name*="service"], select[aria-label*="Service"]')
            if service_select:
                service_select.select_option(label="Workers AI")
                page.wait_for_timeout(500)
            
            # Select Edit access
            print(f"  → Selecting Edit access...")
            access_select = page.query_selector('select[name*="access"], select[aria-label*="Access"]')
            if access_select:
                access_select.select_option(label="Edit")
                page.wait_for_timeout(500)
            
            # Continue to summary
            print(f"  → Continuing to summary...")
            continue_btn = page.query_selector('button:has-text("Continue"), a:has-text("Continue")')
            if continue_btn:
                continue_btn.click()
                page.wait_for_timeout(2000)
            
            # Create token
            print(f"  → Creating token...")
            create_final_btn = page.query_selector('button:has-text("Create Token"), input[value="Create Token"]')
            if create_final_btn:
                create_final_btn.click()
                page.wait_for_timeout(3000)
            
            # Extract token
            print(f"  → Extracting token...")
            token_input = page.query_selector('input[name="token"], input[readonly], code')
            if token_input:
                self.api_token = token_input.input_value() if token_input.evaluate('el => el.tagName') == 'INPUT' else token_input.inner_text()
                self.workers_ai_ok = True
                print(f"  ✓ Token created: {self.api_token[:20]}...")
                return True
            
            # Try to find token in page text
            page_text = page.inner_text('body')
            if "token" in page_text.lower():
                # Look for token pattern
                import re
                token_match = re.search(r'[A-Za-z0-9_\-]{40,}', page_text)
                if token_match:
                    self.api_token = token_match.group()
                    self.workers_ai_ok = True
                    print(f"  ✓ Token found: {self.api_token[:20]}...")
                    return True
            
            print(f"  ❌ Could not extract token")
            return False
            
        except Exception as e:
            print(f"  ❌ Create token error: {e}")
            return False
    
    def export(self) -> Dict:
        """Export account data"""
        self._close_browser()
        return {
            "email": self.email,
            "account_id": self.account_id,
            "api_token": self.api_token,
            "workers_ai_ok": self.workers_ai_ok
        }
    
    def __del__(self):
        """Cleanup"""
        self._close_browser()


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
            grabber._close_browser()
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
            grabber._close_browser()
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
            grabber._close_browser()
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
