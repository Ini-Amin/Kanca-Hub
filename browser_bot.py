#!/usr/bin/env python3
"""Browser automation for Cloudflare account processing with patchright (anti-detection)"""

import json
import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Optional
from patchright.sync_api import sync_playwright, Browser, BrowserContext, Page


class CFAutoGrabber:
    """Automated Cloudflare account grabber with patchright (anti-detection)"""
    
    def __init__(self, email: str, password: str, headless: bool = True, proxy: Optional[Dict] = None):
        self.email = email
        self.password = password
        self.headless = headless
        self.proxy = proxy
        self.account_id = None
        self.api_token = None
        self.workers_ai_ok = False
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
    
    def _start_browser(self):
        """Start browser session with patchright (anti-detection)"""
        if self._browser is None:
            self._playwright = sync_playwright().start()
            
            # Launch options - patchright handles most anti-detection automatically
            launch_args = {
                'headless': self.headless,
            }
            
            # Add proxy if provided
            if self.proxy:
                launch_args['proxy'] = {
                    'server': self.proxy.get('server'),
                    'username': self.proxy.get('username'),
                    'password': self.proxy.get('password'),
                }
                print(f"  → Using proxy: {self.proxy.get('server')}")
            
            self._browser = self._playwright.chromium.launch(**launch_args)
            
            # Context options
            context_args = {
                'viewport': {'width': 1920, 'height': 1080},
                'locale': 'en-US',
                'timezone_id': 'America/New_York',
            }
            
            self._context = self._browser.new_context(**context_args)
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
    
    def _wait_for_challenge(self, timeout=60):
        """Wait for Cloudflare challenge to complete"""
        page = self._page
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            title = page.title()
            
            # Check if challenge is still active
            if "Just a moment" in title or "challenge" in title.lower():
                print(f"  ⏳ Security challenge in progress... ({int(time.time() - start_time)}s)")
                page.wait_for_timeout(2000)
            else:
                # Challenge passed
                print(f"  ✓ Security check passed")
                return True
        
        print(f"  ❌ Challenge timeout after {timeout}s")
        return False
    
    def login(self) -> bool:
        """Login to Cloudflare dashboard with stealth mode"""
        try:
            self._start_browser()
            page = self._page
            
            # Go to login page
            print(f"  → Opening Cloudflare login...")
            page.goto("https://dash.cloudflare.com/login", wait_until="domcontentloaded", timeout=60000)
            
            # Wait for challenge
            if not self._wait_for_challenge():
                return False
            
            # Wait for login form to appear
            print(f"  → Waiting for login form...")
            try:
                page.wait_for_selector('input[type="email"], input[name="email"], input[placeholder*="email"]', timeout=15000)
            except:
                print(f"  ❌ Login form not found")
                return False
            
            # Fill login form - try multiple selectors
            print(f"  → Filling credentials...")
            email_selectors = ['input[type="email"]', 'input[name="email"]', 'input[placeholder*="email"]', 'input[placeholder*="Email"]']
            email_filled = False
            for selector in email_selectors:
                try:
                    page.fill(selector, self.email)
                    email_filled = True
                    break
                except:
                    continue
            
            if not email_filled:
                print(f"  ❌ Could not find email input field")
                return False
            
            password_selectors = ['input[type="password"]', 'input[name="password"]', 'input[placeholder*="password"]']
            password_filled = False
            for selector in password_selectors:
                try:
                    page.fill(selector, self.password)
                    password_filled = True
                    break
                except:
                    continue
            
            if not password_filled:
                print(f"  ❌ Could not find password input field")
                return False
            
            # Wait for Turnstile/CAPTCHA to complete before submitting
            print(f"  → Waiting for Turnstile (4-5s)...")
            page.wait_for_timeout(4500)
            
            # Check if button is enabled (Turnstile solved)
            print(f"  → Checking if Turnstile solved...")
            btn = page.query_selector('button[type="submit"]')
            if btn:
                disabled = btn.get_attribute('disabled')
                if disabled is not None:
                    print(f"  ❌ Turnstile not solved (button disabled)")
                    return False
            
            # Click login button
            print(f"  → Submitting login...")
            login_selectors = ['button[type="submit"]', 'button:has-text("Log In")', 'button:has-text("Login")', 'button:has-text("Sign In")']
            login_clicked = False
            for selector in login_selectors:
                try:
                    page.click(selector, timeout=5000)
                    login_clicked = True
                    print(f"  → Clicked: {selector}")
                    break
                except:
                    continue
            
            if not login_clicked:
                print(f"  ❌ Could not click login button")
                return False
            
            # Wait for redirect
            print(f"  → Waiting for dashboard...")
            page.wait_for_timeout(5000)
            
            current_url = page.url
            print(f"  → Current URL: {current_url}")
            
            # Check if we're logged in
            if "/login" in current_url:
                print(f"  ❌ Still on login page - credentials may be wrong")
                # Take screenshot for debugging
                page.screenshot(path="debug_login_failed.png")
                print(f"  → Screenshot saved: debug_login_failed.png")
                # Check for error messages
                error_text = page.query_selector('.error-message, [data-testid="error"], .alert-danger')
                if error_text:
                    print(f"  → Error message: {error_text.inner_text()}")
                # Extract page content for debugging
                page_content = page.inner_text('body')
                if 'incorrect' in page_content.lower() or 'invalid' in page_content.lower():
                    print(f"  → Page shows authentication error")
                elif 'captcha' in page_content.lower() or 'challenge' in page_content.lower():
                    print(f"  → Page shows CAPTCHA/challenge")
                # Save page content for manual inspection
                with open("debug_page_content.txt", "w") as f:
                    f.write(page_content)
                print(f"  → Page content saved: debug_page_content.txt")
                return False
            
            # Try to extract account ID
            if "/home" in current_url or current_url.endswith("dash.cloudflare.com/"):
                print(f"  → Navigating to get account ID...")
                page.goto("https://dash.cloudflare.com/", wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                current_url = page.url
            
            # Extract account ID from URL
            parts = current_url.split("dash.cloudflare.com/")
            if len(parts) > 1:
                account_part = parts[1].split("/")[0].split("?")[0]
                if account_part and account_part not in ["login", "home", "sign-up", "", "profile"]:
                    self.account_id = account_part
                    print(f"  ✓ Account ID: {self.account_id}")
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


def load_proxy_config(proxy_file: str) -> List[Dict]:
    """Load proxy configuration from JSON file (supports single or multi-proxy)"""
    path = Path(proxy_file)
    if not path.exists():
        print(f"Warning: Proxy file not found: {proxy_file}")
        return []
    
    try:
        with open(path, 'r') as f:
            config = json.load(f)
        
        # Check if it's multi-proxy format (has "proxies" array)
        if 'proxies' in config and isinstance(config['proxies'], list):
            proxies = []
            for p in config['proxies']:
                if p.get('server') and p.get('username') and p.get('password'):
                    proxies.append({
                        'server': p['server'],
                        'username': p['username'],
                        'password': p['password'],
                        'country': p.get('country', 'N/A'),
                        'city': p.get('city', 'N/A')
                    })
            return proxies
        
        # Legacy single-proxy format
        elif config.get('server'):
            return [{
                'server': config['server'],
                'username': config.get('username'),
                'password': config.get('password'),
                'country': 'N/A',
                'city': 'N/A'
            }]
        
        else:
            print(f"Warning: Invalid proxy config format")
            return []
            
    except Exception as e:
        print(f"Warning: Could not load proxy config: {e}")
        return []


def process_accounts(accounts: List[Dict[str, str]], headless: bool = True, proxies: Optional[List[Dict]] = None) -> List[Dict]:
    """Process multiple accounts with proxy rotation and retry"""
    results = []
    total = len(accounts)
    proxy_idx = 0
    max_retries = 3
    
    for idx, account in enumerate(accounts, 1):
        email = account['email']
        password = account['password']
        
        print(f"\n{'='*60}")
        print(f"Processing {idx}/{total}: {email}")
        print('='*60)
        
        success = False
        for attempt in range(1, max_retries + 1):
            # Pick proxy (rotate through list)
            proxy = None
            if proxies and len(proxies) > 0:
                proxy = proxies[proxy_idx % len(proxies)]
                proxy_idx += 1
            
            if proxy:
                print(f"  Attempt {attempt}/{max_retries} - Proxy: {proxy.get('server')} ({proxy.get('country')}/{proxy.get('city')})")
            else:
                print(f"  Attempt {attempt}/{max_retries} - No proxy")
            
            grabber = CFAutoGrabber(email, password, headless, proxy)
            
            # Step 1: Login
            print(f"  [1/4] Logging in...")
            if not grabber.login():
                print(f"  ❌ Login failed (attempt {attempt})")
                grabber._close_browser()
                if attempt < max_retries:
                    print(f"  → Retrying with different proxy...")
                    time.sleep(2)
                continue
            print(f"  ✓ Login successful")
            
            # Step 2: Get Account ID
            print(f"  [2/4] Getting Account ID...")
            if not grabber.get_account_id():
                print(f"  ❌ Failed to get Account ID")
                grabber._close_browser()
                break
            print(f"  ✓ Account ID: {grabber.account_id}")
            
            # Step 3: Create Token
            print(f"  [3/4] Creating API token...")
            if not grabber.create_workers_ai_token():
                print(f"  ❌ Failed to create token")
                grabber._close_browser()
                break
            print(f"  ✓ Token created")
            
            # Step 4: Export
            print(f"  [4/4] Exporting...")
            result = grabber.export()
            results.append(result)
            print(f"  ✓ Exported")
            success = True
            break
        
        if success:
            print(f"\n  ✅ Success: {email}")
        else:
            print(f"\n  ❌ All {max_retries} attempts failed for {email}")
            results.append({
                'email': email,
                'status': 'all_attempts_failed'
            })
    
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
    parser.add_argument("--accounts", help="Path to accounts file (JSON or TXT)")
    parser.add_argument("--single", help="Single account in email:password format")
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    parser.add_argument("--proxy", help="Path to proxy config JSON file")
    
    args = parser.parse_args()
    
    # Load proxy if provided
    proxies = None
    if args.proxy:
        proxies = load_proxy_config(args.proxy)
        if proxies:
            print(f"✓ Loaded {len(proxies)} proxies from {args.proxy}")
            for p in proxies:
                print(f"  → {p.get('server')} ({p.get('country')}/{p.get('city')})")
        else:
            print(f"⚠️  Could not load proxies from {args.proxy}")
    
    # Single account mode
    if args.single:
        if ':' not in args.single:
            print("Error: Invalid format. Use email:password")
            sys.exit(1)
        
        email, password = args.single.split(':', 1)
        email = email.strip()
        password = password.strip()
        
        if not email or not password:
            print("Error: Email and password cannot be empty")
            sys.exit(1)
        
        print(f"Processing single account: {email}")
        print("=" * 60)
        
        accounts = [{'email': email, 'password': password}]
        results = process_accounts(accounts, headless=args.headless, proxies=proxies)
        sys.exit(0 if results else 1)
    
    # Bulk accounts mode
    if args.accounts:
        accounts = load_accounts(args.accounts)
        print(f"Loaded {len(accounts)} accounts from {args.accounts}")
        
        results = process_accounts(accounts, headless=args.headless, proxies=proxies)
        sys.exit(0 if results else 1)
    
    # No arguments provided
    print("Error: Please provide --accounts <file> or --single <email:password>")
    sys.exit(1)
