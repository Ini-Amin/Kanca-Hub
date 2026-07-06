"""
Cloudflare Signup Flow — Automated account creation via headless browser.

Handles:
1. Navigate to sign-up page
2. Fill email + password
3. Solve Turnstile CAPTCHA + inject token into form
4. Submit form
5. Handle post-submit challenges (second Turnstile, JS challenge, interstitial)
6. Extract Account ID from redirect URL
"""

import asyncio
import re
from typing import Optional

import nodriver as uc

from .turnstile_bypass import (
    verify_cf,
    is_turnstile_present,
    extract_turnstile_token,
    inject_turnstile_token,
)


CLOUDFLARE_SIGNUP_URL = "https://dash.cloudflare.com/sign-up"


class SignupResult:
    """Result of a signup attempt."""

    def __init__(
        self,
        success: bool,
        email: str = "",
        password: str = "",
        account_id: str = "",
        error: str = "",
        page_url: str = "",
    ):
        self.success = success
        self.email = email
        self.password = password
        self.account_id = account_id
        self.error = error
        self.page_url = page_url

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "email": self.email,
            "password": self.password,
            "account_id": self.account_id,
            "error": self.error,
        }


async def _handle_post_submit_challenges(page: uc.Tab, max_wait: int = 30) -> bool:
    """
    After submitting the signup form, Cloudflare may show:
    - Second Turnstile challenge
    - JS Challenge ("Checking your connection...")
    - "We're setting up your account" interstitial
    
    Returns True if we should continue waiting for redirect.
    """
    for _ in range(max_wait):
        url = await page.evaluate("location.href")
        
        # Already redirected — done
        if "/sign-up" not in url:
            return True
        
        # Check for new Turnstile
        turnstile = await page.evaluate("""
            (() => {
                const iframes = document.querySelectorAll("iframe");
                for (const f of iframes) {
                    if (f.src && f.src.includes("challenges.cloudflare.com")) return true;
                }
                const body = document.body ? document.body.innerText : '';
                return body.includes("Verify you are human");
            })()
        """)
        
        if turnstile:
            print("    🔄 Post-submit Turnstile detected, solving...")
            try:
                await verify_cf(page, timeout=30)
                token = await extract_turnstile_token(page, timeout=10)
                if token:
                    await inject_turnstile_token(page, token)
                await asyncio.sleep(2)
                # Re-submit form after solving post-submit Turnstile
                try:
                    submit_btn = await page.select('button[type="submit"]', timeout=3)
                    if submit_btn:
                        await submit_btn.click()
                        print("    📤 Form re-submitted after Turnstile")
                except Exception:
                    pass
                await asyncio.sleep(3)
            except Exception as e:
                print(f"    ⚠️ Post-submit Turnstile failed: {e}")
        
        # Check for JS Challenge interstitial
        challenge_text = await page.evaluate("""
            (() => {
                const body = document.body ? document.body.innerText : '';
                if (body.includes("Checking your connection") ||
                    body.includes("Just a moment") ||
                    body.includes("Verifying")) return true;
                return false;
            })()
        """)
        
        if challenge_text:
            print("    ⏳ JS Challenge interstitial, waiting...")
            await asyncio.sleep(5)
        
        await asyncio.sleep(1)
    
    return False


async def signup(
    page: uc.Tab,
    email: str,
    password: str,
    max_wait: int = 30,
    retry_turnstile: bool = True,
) -> SignupResult:
    """
    Execute the Cloudflare signup flow.

    Args:
        page: nodriver Tab (will navigate to sign-up)
        email: Email address for the new account
        password: Password for the new account
        max_wait: Max seconds to wait for redirect after submit
        retry_turnstile: Whether to retry Turnstile if first attempt fails

    Returns:
        SignupResult with account_id on success
    """
    # Navigate to signup (skip if already there)
    current_url = await page.evaluate("location.href")
    if "sign-up" not in current_url and "signup" not in current_url.lower():
        await page.get(CLOUDFLARE_SIGNUP_URL)
        await asyncio.sleep(8)
    else:
        print("    📍 Already on signup page, reusing session")
        await asyncio.sleep(3)

    # Fill email
    email_input = await page.select('input[name="email"]', timeout=15)
    if not email_input:
        return SignupResult(False, email=email, error="Email input not found")
    await email_input.click()
    await asyncio.sleep(0.5)
    await email_input.send_keys(email)
    await asyncio.sleep(1)

    # Fill password
    pw_input = await page.select('input[name="password"]', timeout=5)
    if not pw_input:
        return SignupResult(False, email=email, error="Password input not found")
    await pw_input.click()
    await asyncio.sleep(0.5)
    await pw_input.send_keys(password)
    await asyncio.sleep(2)

    # Scroll to make Turnstile visible
    await page.evaluate("window.scrollBy(0, 400)")
    await asyncio.sleep(3)

    # Solve Turnstile
    turnstile_present = await is_turnstile_present(page)
    if turnstile_present:
        token = None
        for attempt in range(2 if retry_turnstile else 1):
            try:
                token = await verify_cf(page, timeout=60)
                print(f"    ✅ Turnstile solved: {token[:20] if token else 'no-token'}...")
                break
            except (TimeoutError, RuntimeError) as e:
                if attempt == 0 and retry_turnstile:
                    await asyncio.sleep(5)
                    continue
                return SignupResult(False, email=email, error=f"Turnstile failed: {e}")

        # Wait for Turnstile callback to populate hidden input (with fallback)
        if token:
            injected_token = await extract_turnstile_token(page, timeout=10)
            if not injected_token:
                # Callback didn't fire — manually inject
                print("    ⚠️ Turnstile callback didn't fire, injecting token manually...")
                await inject_turnstile_token(page, token)
            else:
                print(f"    ✅ Turnstile token in DOM: {injected_token[:20]}...")
        else:
            # verify_cf returned no token but didn't error — might have solved silently
            dom_token = await extract_turnstile_token(page, timeout=10)
            if not dom_token:
                return SignupResult(False, email=email, error="Turnstile solved but no token in DOM")

    await asyncio.sleep(3)

    # Submit form — try multiple methods
    submitted = False
    for btn_selector in ['button[type="submit"]', 'button:has-text("Sign Up")', 
                          'input[type="submit"]', 'button[data-testid="sign-up-button"]']:
        try:
            submit_btn = await page.select(btn_selector, timeout=5)
            if submit_btn:
                await submit_btn.scroll_into_view()
                await asyncio.sleep(1)
                await submit_btn.click()
                submitted = True
                print(f"    📤 Form submitted via {btn_selector}")
                break
        except Exception:
            continue

    if not submitted:
        # Last resort: press Enter on the form or use JS submit
        try:
            await page.evaluate("""
                const form = document.querySelector('form');
                if (form) form.submit();
            """)
            submitted = True
            print("    📤 Form submitted via JS")
        except Exception:
            return SignupResult(False, email=email, error="Submit button not found and JS submit failed")

    # Wait for redirect — handle interstitials
    redirect_ok = await _handle_post_submit_challenges(page, max_wait=max_wait)
    
    # Extra wait for dashboard to fully load
    await asyncio.sleep(8)

    # Extract Account ID from URL
    url = await page.evaluate("location.href")
    match = re.search(r"/([a-f0-9]{32})", url)
    if match:
        account_id = match.group(1)
        return SignupResult(
            True,
            email=email,
            password=password,
            account_id=account_id,
            page_url=url,
        )

    # If URL still has /sign-up, check if form errors are displayed
    if "/sign-up" in url:
        error_msgs = await page.evaluate("""
            Array.from(document.querySelectorAll('p, [role="alert"], .error-message, .form-error'))
                .map(e => e.textContent.trim())
                .filter(t => t.length > 5 && 
                    (t.toLowerCase().includes('unable') || 
                     t.toLowerCase().includes('limit') || 
                     t.toLowerCase().includes('incorrect') ||
                     t.toLowerCase().includes('try again') ||
                     t.toLowerCase().includes('captcha') ||
                     t.toLowerCase().includes('invalid') ||
                     t.toLowerCase().includes('failed')))
        """)
        error = error_msgs if error_msgs else f"Form rejected, stayed on sign-up page"
    else:
        # Redirected somewhere but no account ID
        error = f"Redirected to unexpected page: {url[:120]}"

    return SignupResult(False, email=email, error=(str(error) if error else f"Redirect failed: {url[:80]}"))
