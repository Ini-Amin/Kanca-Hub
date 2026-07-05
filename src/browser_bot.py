#!/usr/bin/env python3
"""Browser automation for Cloudflare account processing with patchright (anti-detection)"""

import sys
import asyncio
import re
import time
from pathlib import Path
from typing import List, Dict, Optional
from patchright.sync_api import sync_playwright, Browser, BrowserContext, Page

from .turnstile_solver import extract_sitekey, solve_turnstile_isolated, solve_turnstile_manual
from .utils import load_accounts, load_proxy_config, save_results


# Fix for Windows: patchright needs ProactorEventLoop for subprocess support
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())


class CFAutoGrabber:
    """Automated Cloudflare account grabber with patchright (anti-detection)"""
    
    def __init__(self, email: str, password: str, headless: bool = True, proxy: Optional[Dict] = None, login_method: str = "email"):
        self.email = email
        self.password = password
        self.headless = headless
        self.proxy = proxy
        self.login_method = login_method
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
            import os, subprocess
            
            # Auto-start Xvfb for headful mode (Turnstile bypass)
            if not os.environ.get('DISPLAY'):
                # Check if Xvfb already running on :99
                result = subprocess.run(['pgrep', '-f', 'Xvfb :99'], capture_output=True)
                if result.returncode != 0:
                    # Start Xvfb
                    try:
                        subprocess.Popen(['Xvfb', ':99', '-screen', '0', '1920x1080x24'],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        time.sleep(0.5)
                    except FileNotFoundError:
                        print("  ⚠️  Xvfb not found, using headless mode")
                os.environ['DISPLAY'] = ':99'
            
            has_display = bool(os.environ.get('DISPLAY'))
            use_headless = not has_display if self.headless else self.headless
            if has_display and self.headless:
                print("  → Using Xvfb (virtual display) for Turnstile bypass")
                use_headless = False
            
            self._playwright = sync_playwright().start()
            
            # Launch options
            launch_args = {
                'headless': use_headless,
                'args': ['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
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
    
    def _wait_for_challenge(self, timeout=120):
        """Wait for Cloudflare challenge to complete"""
        page = self._page
        start_time = time.time()
        last_check = ""
        
        while time.time() - start_time < timeout:
            title = page.title()
            url = page.url
            
            # Check if challenge is still active
            if "Just a moment" in title or "challenge" in title.lower() or "Attention Required" in title:
                if title != last_check:
                    print(f"  ⏳ Security challenge in progress... ({int(time.time() - start_time)}s)")
                    last_check = title
                page.wait_for_timeout(2000)
            elif "login" in url.lower() or "dash.cloudflare" in url:
                # Challenge passed
                print(f"  ✓ Security check passed")
                return True
            else:
                # Still waiting
                page.wait_for_timeout(1000)
        
        # Timeout - take screenshot for debugging
        print(f"  ❌ Challenge timeout after {timeout}s")
        try:
            page.screenshot(path="debug_challenge_timeout.png")
            print(f"  📸 Debug screenshot saved: debug_challenge_timeout.png")
        except:
            pass
        return False
    
    def login(self) -> bool:
        """Login to Cloudflare dashboard with stealth mode"""
        try:
            self._start_browser()
            page = self._page
            
            # Go to login page
            print(f"  → Opening Cloudflare login...")
            page.goto("https://dash.cloudflare.com/login", wait_until="domcontentloaded", timeout=60000)
            
            # Wait for Turnstile challenge to auto-pass (Xvfb handles this)
            print(f"  → Page title: {page.title()}")
            if not self._wait_for_challenge():
                print(f"  ⚠️  Challenge timeout, trying manual flow...")
                if not self._login_manual_turnstile():
                    return False
            
            # Fill credentials
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
            
            # Small delay after filling
            page.wait_for_timeout(1000)
            
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
                page.screenshot(path="debug_login_failed.png")
                print(f"  → Screenshot saved: debug_login_failed.png")
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
    
    def _login_manual_turnstile(self) -> bool:
        """Fallback: Manual Turnstile solving (old approach)"""
        page = self._page
        
        if not solve_turnstile_manual(page):
            return False
        
        # Fill credentials
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
        
        page.wait_for_timeout(1000)
        
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
        
        if "/login" in current_url:
            print(f"  ❌ Still on login page")
            return False
        
        # Extract account ID
        if "/home" in current_url or current_url.endswith("dash.cloudflare.com/"):
            page.goto("https://dash.cloudflare.com/", wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
            current_url = page.url
        
        parts = current_url.split("dash.cloudflare.com/")
        if len(parts) > 1:
            account_part = parts[1].split("/")[0].split("?")[0]
            if account_part and account_part not in ["login", "home", "sign-up", "", "profile"]:
                self.account_id = account_part
                print(f"  ✓ Account ID: {self.account_id}")
                return True
        
        return False
    
    def login_google(self) -> bool:
        """Login to Cloudflare via Google OAuth (fully automated)"""
        try:
            self._start_browser()
            page = self._page

            print(f"  → Opening Cloudflare login...")
            page.goto("https://dash.cloudflare.com/login", wait_until="domcontentloaded", timeout=60000)
            
            # Wait for page load
            page.wait_for_timeout(3000)

            # Click "Continue with Google"
            print(f"  → Clicking 'Continue with Google'...")
            google_btn = page.query_selector('button:has-text("Continue with Google"), a:has-text("Continue with Google")')
            if google_btn:
                google_btn.click()
            else:
                # Fallback: try clicking by href
                page.click('a[href*="accounts.google.com"]')
            
            # Wait for Google login page/popup
            print(f"  → Waiting for Google login...")
            page.wait_for_load_state("networkidle", timeout=15000)
            page.wait_for_timeout(2000)

            current_url = page.url
            if "accounts.google.com" not in current_url:
                # Sometimes opens in new tab/popup
                for p in self._context.pages:
                    if "accounts.google.com" in p.url:
                        page = p
                        self._page = p
                        break

            # Fill Email
            print(f"  → Filling Google email...")
            page.wait_for_selector('input[type="email"]', timeout=10000)
            page.fill('input[type="email"]', self.email)
            page.click('button:has-text("Next"), #identifierNext')
            page.wait_for_timeout(2000)

            # Fill Password
            print(f"  → Filling Google password...")
            page.wait_for_selector('input[type="password"]', timeout=10000)
            page.fill('input[type="password"]', self.password)
            page.click('button:has-text("Next"), #passwordNext')
            
            # Wait for redirect back to Cloudflare or 2FA prompt
            print(f"  → Waiting for authentication...")
            page.wait_for_load_state("networkidle", timeout=20000)
            page.wait_for_timeout(3000)

            # Check for Google 2FA / Phone verification
            if "challenge" in page.url or "signin/v2" in page.url or "Verify" in page.title():
                print(f"  ⚠️  Google 2FA / Phone verification detected")
                print(f"  ⚠️  Automated bypass not available. Please verify manually or use App Password.")
                # Wait 60s for manual intervention
                print(f"  → Waiting 60s for manual verification...")
                page.wait_for_timeout(60000)

            # Wait for redirect back to Cloudflare
            print(f"  → Waiting for Cloudflare redirect...")
            page.wait_for_url("**/dash.cloudflare.com/**", timeout=30000)
            page.wait_for_timeout(3000)

            # Extract Account ID
            current_url = page.url
            parts = current_url.split("dash.cloudflare.com/")
            if len(parts) > 1:
                account_part = parts[1].split("/")[0].split("?")[0]
                if account_part and account_part not in ["login", "home", "sign-up", "", "profile"]:
                    self.account_id = account_part
                    print(f"  ✓ Logged in via Google | Account ID: {self.account_id}")
                    return True

            print(f"  ❌ Google login redirect failed or account ID not found")
            return False

        except Exception as e:
            print(f"  ❌ Google login error: {e}")
            return False
    
    def get_account_id(self) -> bool:
        """Get account ID (already done during login)"""
        if self.account_id:
            return True
        
        try:
            page = self._page
            if page is None:
                return False
            
            current_url = page.url
            
            parts = current_url.split("dash.cloudflare.com/")
            if len(parts) > 1:
                account_part = parts[1].split("/")[0].split("?")[0]
                if account_part and account_part not in ["login", "home", "sign-up", "profile", ""]:
                    self.account_id = account_part
                    return True
            
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
    
    def create_custom_api_token(self) -> bool:
        """Create Custom API token (legacy permission wizard approach)"""
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
                page.fill('input[placeholder*="name"]', token_name)
            
            page.wait_for_timeout(1000)
            
            # Add permission
            print(f"  → Adding Workers AI permission...")
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
    
    def create_workers_ai_api_token(self) -> bool:
        """Create Workers AI API Token via dedicated Workers AI page (ZemCFLare flow)
        
        Navigates to /ai/workers-ai/usage → REST API tab → direct token creation.
        Simpler than the Custom Token wizard — no permission selection needed.
        """
        try:
            page = self._page
            if page is None:
                print(f"  ❌ Browser not started")
                return False
            
            if not self.account_id:
                print(f"  ❌ No account ID available")
                return False
            
            # Navigate to Workers AI usage page
            usage_url = f"https://dash.cloudflare.com/{self.account_id}/ai/workers-ai/usage"
            print(f"  → Navigating to Workers AI page...")
            page.goto(usage_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)
            
            # Handle potential challenges
            if "Just a moment" in page.title() or "challenge" in page.title().lower():
                print(f"  ⏳ Turnstile challenge detected, waiting...")
                if not self._wait_for_challenge():
                    print(f"  ❌ Challenge timeout")
                    return False
            
            # Click "REST API" tab
            print(f"  → Switching to REST API tab...")
            rest_tab = page.query_selector('span:has-text("REST API"), button:has-text("REST API"), a:has-text("REST API")')
            if rest_tab:
                rest_tab.click()
                page.wait_for_timeout(2000)
            else:
                # Try by role
                rest_tab = page.query_selector('[role="tab"]:has-text("REST API")')
                if rest_tab:
                    rest_tab.click()
                    page.wait_for_timeout(2000)
                else:
                    print(f"  ⚠️  REST API tab not found, trying alternate selectors...")
                    page.screenshot(path="debug_rest_api_tab.png")
            
            # Click "Create a Workers AI API Token" button
            print(f"  → Creating Workers AI API Token...")
            create_btn = page.query_selector(
                'button:has-text("Create a Workers AI API Token"), '
                'a:has-text("Create a Workers AI API Token"), '
                'button:has-text("Create API Token")'
            )
            if create_btn:
                create_btn.click()
                page.wait_for_timeout(3000)
            else:
                # Fallback: find any create button
                create_btn = page.query_selector(
                    '[data-testid="create-token-button"], '
                    'button[type="button"]:has-text("Create")'
                )
                if create_btn:
                    create_btn.click()
                    page.wait_for_timeout(3000)
                else:
                    print(f"  ❌ Create token button not found")
                    page.screenshot(path="debug_create_token_btn.png")
                    return False
            
            # Fill token name
            token_name = f"WorkersAI-{self.email.split('@')[0]}-{int(time.time())}"
            print(f"  → Token name: {token_name}")
            
            name_input = page.query_selector('input[name="name"], input[id="name"], input[placeholder*="name"]')
            if name_input:
                name_input.fill(token_name)
                page.wait_for_timeout(500)
            else:
                # Try any visible text input
                inputs = page.query_selector_all('input[type="text"]')
                for inp in inputs:
                    try:
                        if inp.is_visible():
                            inp.fill(token_name)
                            break
                    except:
                        continue
            
            # Click "Create API Token" / "Create" button
            print(f"  → Submitting token creation...")
            submit_btn = page.query_selector(
                'button:has-text("Create API Token"), '
                'button[type="submit"]:has-text("Create"), '
                'button:has-text("Create token")'
            )
            if submit_btn:
                submit_btn.click()
                page.wait_for_timeout(4000)
            
            # Copy the token — try clipboard button first
            print(f"  → Extracting token...")
            token_value = None
            
            # Method 1: Click "Copy API Token" button, read clipboard via JS
            copy_btn = page.query_selector(
                'button:has-text("Copy API Token"), '
                'button:has-text("Copy"), '
                '[data-testid="copy-token-button"]'
            )
            if copy_btn:
                copy_btn.click()
                page.wait_for_timeout(1000)
                # Read clipboard via JS
                try:
                    token_value = page.evaluate("() => navigator.clipboard.readText()")
                except:
                    pass
            
            # Method 2: Look for token in input/code elements
            if not token_value:
                token_input = page.query_selector('input[readonly], input[name="token"], code, pre')
                if token_input:
                    token_value = token_input.input_value() if token_input.evaluate('el => el.tagName') == 'INPUT' else token_input.inner_text()
            
            # Method 3: Regex search in page
            if not token_value:
                page_text = page.inner_text('body')
                token_match = re.search(r'[A-Za-z0-9_\-]{40,}', page_text)
                if token_match:
                    token_value = token_match.group()
            
            if not token_value:
                print(f"  ❌ Could not extract token")
                page.screenshot(path="debug_token_extract.png")
                return False
            
            self.api_token = token_value.strip()
            self.workers_ai_ok = True
            print(f"  ✓ Workers AI Token: {self.api_token[:20]}...{self.api_token[-10:]}")
            
            # Click "Finish" / close button if present
            try:
                finish_btn = page.query_selector('button:has-text("Finish"), button:has-text("Done"), button:has-text("Close")')
                if finish_btn:
                    finish_btn.click()
                    page.wait_for_timeout(1000)
            except:
                pass
            
            return True
            
        except Exception as e:
            print(f"  ❌ Workers AI token creation error: {e}")
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


def process_accounts(accounts: List[Dict[str, str]], headless: bool = True, proxies: Optional[List[Dict]] = None, login_method: str = "email") -> List[Dict]:
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
            proxy = None
            if proxies and len(proxies) > 0:
                proxy = proxies[proxy_idx % len(proxies)]
                proxy_idx += 1
            
            if proxy:
                print(f"  Attempt {attempt}/{max_retries} - Proxy: {proxy.get('server')} ({proxy.get('country')}/{proxy.get('city')})")
            else:
                print(f"  Attempt {attempt}/{max_retries} - No proxy")
            
            grabber = CFAutoGrabber(email, password, headless, proxy, login_method)
            
            print(f"  [1/4] Logging in via {login_method}...")
            if login_method == "google":
                login_success = grabber.login_google()
            else:
                login_success = grabber.login()
            
            if not login_success:
                print(f"  ❌ Login failed (attempt {attempt})")
                grabber._close_browser()
                if attempt < max_retries:
                    print(f"  → Retrying with different proxy...")
                    time.sleep(2)
                continue
            print(f"  ✓ Login successful")
            
            print(f"  [2/4] Getting Account ID...")
            if not grabber.get_account_id():
                print(f"  ❌ Failed to get Account ID")
                grabber._close_browser()
                break
            print(f"  ✓ Account ID: {grabber.account_id}")
            
            print(f"  [3/4] Creating API token (Workers AI flow)...")
            if not grabber.create_workers_ai_api_token():
                print(f"  ⚠️  Workers AI flow failed, trying Custom Token...")
                if not grabber.create_custom_api_token():
                    print(f"  ❌ Failed to create token")
                    grabber._close_browser()
                    break
            print(f"  ✓ Token created")
            
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
    save_results(results)
    
    return results
