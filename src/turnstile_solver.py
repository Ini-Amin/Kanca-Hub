#!/usr/bin/env python3
"""Turnstile solver using isolated page approach (from Theyka/Turnstile-Solver)"""

import re
import time
from typing import Optional
from patchright.sync_api import Page, BrowserContext


# Turnstile Solver HTML template
TURNSTILE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Turnstile Solver</title>
    <script src="https://challenges.cloudflare.com/turnstile/v0/api.js" async></script>
</head>
<body>
    <!-- cf turnstile -->
</body>
</html>"""


def extract_sitekey(page: Page) -> Optional[str]:
    """Extract Turnstile sitekey from page using multiple methods"""
    try:
        # Method 1: Look for data-sitekey attribute
        turnstile_div = page.query_selector('[data-sitekey]')
        if turnstile_div:
            sitekey = turnstile_div.get_attribute('data-sitekey')
            if sitekey:
                return sitekey
        
        # Method 2: Look in script tags
        scripts = page.query_selector_all('script')
        for script in scripts:
            content = script.inner_text()
            match = re.search(r'sitekey["\s:]+["\']?([0-9A-Za-z_-]+)', content)
            if match:
                return match.group(1)
        
        # Method 3: Look in page source
        content = page.content()
        match = re.search(r'data-sitekey=["\']([0-9A-Za-z_-]+)', content)
        if match:
            return match.group(1)
        
        # Method 4: Look for Turnstile iframe
        iframe = page.query_selector('iframe[src*="challenges.cloudflare.com"]')
        if iframe:
            src = iframe.get_attribute('src')
            match = re.search(r'sitekey=([0-9A-Za-z_-]+)', src)
            if match:
                return match.group(1)
        
    except Exception as e:
        print(f"  ⚠️  Could not extract sitekey: {e}")
    
    return None


def solve_turnstile_isolated(context: BrowserContext, url: str, sitekey: str) -> Optional[str]:
    """Solve Turnstile using isolated page approach"""
    print(f"  → Solving Turnstile in isolated page...")
    
    # Create a new page for solving
    solver_page = context.new_page()
    
    try:
        # Prepare Turnstile HTML
        turnstile_div = f'<div class="cf-turnstile" data-sitekey="{sitekey}"></div>'
        page_data = TURNSTILE_HTML.replace("<!-- cf turnstile -->", turnstile_div)
        
        # Route the URL to serve our custom HTML
        url_with_slash = url + "/" if not url.endswith("/") else url
        solver_page.route(url_with_slash, lambda route: route.fulfill(body=page_data, status=200))
        solver_page.goto(url_with_slash)
        
        # Wait for Turnstile to solve
        token = None
        for attempt in range(15):  # 30 seconds max
            solver_page.wait_for_timeout(2000)
            
            # Try to click turnstile to trigger
            try:
                turnstile_div = solver_page.query_selector('.cf-turnstile')
                if turnstile_div:
                    turnstile_div.click()
            except:
                pass
            
            # Check for token
            try:
                token_value = solver_page.input_value('[name="cf-turnstile-response"]')
                if token_value and token_value != "":
                    token = token_value
                    print(f"  ✓ Turnstile solved ({(attempt + 1) * 2}s)")
                    break
            except:
                pass
            
            if attempt < 8:
                print(f"  ⏳ Solving... ({(attempt + 1) * 2}s)")
        
        return token
        
    except Exception as e:
        print(f"  ❌ Turnstile solver error: {e}")
        return None
    finally:
        try:
            solver_page.close()
        except:
            pass


def solve_turnstile_manual(page: Page) -> bool:
    """Fallback: Manual Turnstile solving (old approach)"""
    print(f"  → Waiting for Turnstile widget...")
    turnstile_wait_start = time.time()
    turnstile_appeared = False
    
    for _ in range(15):
        try:
            turnstile_div = page.query_selector('.cf-turnstile, iframe[src*="challenges.cloudflare.com"]')
            if turnstile_div:
                turnstile_appeared = True
                print(f"  ✓ Turnstile widget appeared ({int(time.time() - turnstile_wait_start)}s)")
                break
        except:
            pass
        page.wait_for_timeout(2000)
    
    if not turnstile_appeared:
        print(f"  ⚠️  Turnstile widget not detected, proceeding anyway...")
    
    print(f"  → Solving Turnstile manually...")
    turnstile_solved = False
    
    for attempt in range(15):
        page.wait_for_timeout(2000)
        
        try:
            turnstile_div = page.query_selector('.cf-turnstile, iframe[src*="challenges.cloudflare.com"]')
            if turnstile_div:
                turnstile_div.click()
                if attempt == 0:
                    print(f"  → Clicked turnstile widget")
        except:
            pass
        
        try:
            turnstile_value = page.input_value('[name="cf-turnstile-response"]')
            if turnstile_value and turnstile_value != "":
                turnstile_solved = True
                print(f"  ✓ Turnstile solved ({(attempt + 1) * 2}s)")
                break
        except:
            pass
        
        btn = page.query_selector('button[type="submit"]')
        if btn:
            disabled = btn.get_attribute('disabled')
            if disabled is None:
                turnstile_solved = True
                print(f"  ✓ Turnstile solved (button enabled) ({(attempt + 1) * 2}s)")
                break
        
        if attempt < 8:
            print(f"  ⏳ Turnstile solving... ({(attempt + 1) * 2}s)")
    
    if not turnstile_solved:
        print(f"  ❌ Turnstile not solved after 30s")
        try:
            page.screenshot(path="debug_turnstile_timeout.png")
        except:
            pass
        return False
    
    return True
