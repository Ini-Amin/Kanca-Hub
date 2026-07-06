"""
Cloudflare JS Challenge Bypass — Pre-flight cf_clearance via Patchright.

The signup page at dash.cloudflare.com/sign-up throws a "Just a moment..."
JS Challenge interstitial BEFORE showing the signup form. nodriver's built-in
verify_cf() only handles Turnstile widgets, not this interstitial.

Patchright (Playwright fork) with real Chrome + Windows UA bypasses it
instantly. This module extracts the cf_clearance cookie for use in the
main nodriver flow.

Strategy:
1. Launch Patchright (headless=False, Chrome 150, Windows UA)
2. Navigate to signup page → wait for cf_clearance cookie
3. Export cookies to JSON file
4. nodriver picks up cookies before starting its flow

Requires: pip install patchright
Requires: google-chrome-stable installed on Linux
"""

import json
import os
import time
from pathlib import Path
from typing import Optional

try:
    from patchright.sync_api import sync_playwright
    PATCHRIGHT_AVAILABLE = True
except ImportError:
    PATCHRIGHT_AVAILABLE = False


def bypass_js_challenge(
    url: str = "https://dash.cloudflare.com/sign-up",
    cookie_file: str = "/tmp/cf_cookies.json",
    timeout: int = 45,
    display: str = ":99",
) -> Optional[dict]:
    """
    Bypass CF JS Challenge interstitial and export cookies.

    Returns dict with cookies if successful, None on failure.
    """
    if not PATCHRIGHT_AVAILABLE:
        print("    ⚠️ patchright not installed, skipping JS Challenge bypass")
        return None

    os.environ["DISPLAY"] = display

    p = sync_playwright().start()
    result = None

    try:
        browser = p.chromium.launch(
            headless=False,
            channel="chrome",
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--window-size=1920,1080",
                "--disable-dev-shm-usage",
            ],
        )
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/150.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/New_York",
        )

        page = ctx.new_page()
        page.add_init_script(
            'Object.defineProperty(navigator, "webdriver", {get: () => false});'
        )

        page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")

        # Wait for cf_clearance cookie
        start = time.time()
        while time.time() - start < timeout:
            cookies = ctx.cookies()
            cf_cookie = [c for c in cookies if c["name"] == "cf_clearance"]
            if cf_cookie:
                elapsed = time.time() - start
                print(f"    ✅ cf_clearance obtained in {elapsed:.1f}s")

                # Save all cookies for nodriver import
                cookie_list = []
                for c in cookies:
                    cookie_entry = {
                        "name": c["name"],
                        "value": c["value"],
                        "domain": c.get("domain", ""),
                        "path": c.get("path", "/"),
                        "expires": c.get("expires", -1),
                        "httpOnly": c.get("httpOnly", False),
                        "secure": c.get("secure", False),
                        "sameSite": c.get("sameSite", "Lax"),
                    }
                    cookie_list.append(cookie_entry)

                Path(cookie_file).write_text(json.dumps(cookie_list, indent=2))
                result = {
                    "cf_clearance": cf_cookie[0]["value"],
                    "cookie_file": cookie_file,
                    "cookies": cookie_list,
                }
                break
            time.sleep(1)

        if not result:
            print("    ⚠️ cf_clearance not obtained within timeout")

    except Exception as e:
        print(f"    ⚠️ JS Challenge bypass error: {e}")

    finally:
        try:
            browser.close()
        except Exception:
            pass
        try:
            p.stop()
        except Exception:
            pass

    return result


def load_cookies_into_nodriver(page, cookie_file: str = "/tmp/cf_cookies.json") -> bool:
    """
    Load exported cf_clearance cookies into a nodriver page.

    nodriver's set_cookies expects a list of dicts.
    """
    cookie_path = Path(cookie_file)
    if not cookie_path.exists():
        return False

    try:
        cookies = json.loads(cookie_path.read_text())
        # Convert to the format nodriver expects
        for c in cookies:
            c.pop("httpOnly", None)
            c.pop("sameSite", None)
            # nodriver doesn't use httpOnly/sameSite keys
        page.set_cookies(cookies)
        return True
    except Exception as e:
        print(f"    ⚠️ Failed to load cookies into nodriver: {e}")
        return False
