"""
Cloudflare Signup Flow — Submit-first strategy.

Flow:
1. Navigate to sign-up page
2. Fill email + password
3. Quick Turnstile interaction (don't block on solve)
4. Submit form IMMEDIATELY
5. If redirect fails → proper Turnstile solve + resubmit

The insight: CF signup is lenient. A partially-interacted Turnstile
often passes. Only fall back to full solve if the initial submit fails.
"""

import asyncio
import re
from typing import Optional

import nodriver as uc

from .turnstile_bypass import solve_turnstile, is_turnstile_present

CLOUDFLARE_SIGNUP_URL = "https://dash.cloudflare.com/sign-up"


def _unwrap(val):
    """Unwrap nodriver evaluate result dict to primitive value."""
    if isinstance(val, dict) and "value" in val:
        return val["value"]
    return val


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
        account_created: bool = False,
    ):
        self.success = success
        self.email = email
        self.password = password
        self.account_id = account_id
        self.error = error
        self.page_url = page_url
        # True when Cloudflare accepted the signup (account exists) even if the
        # 32-char account id could not be resolved yet (e.g. redirect to
        # /?to=%2F%3Aaccount%2Fhome — user still needs to verify email first).
        self.account_created = account_created

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "email": self.email,
            "password": self.password,
            "account_id": self.account_id,
            "error": self.error,
            "account_created": self.account_created,
        }


async def _fill_form(page: uc.Tab, email: str, password: str) -> Optional[str]:
    """Fill email + password. Returns error string or None."""
    # Email input — retry with delay (CF React form slow to mount in headless)
    email_input = None
    for attempt in range(6):
        email_input = await page.select('input[name="email"]', timeout=10)
        if email_input:
            break
        await asyncio.sleep(3)
    if not email_input:
        return "Email input not found"
    await email_input.click()
    await asyncio.sleep(0.5)
    await email_input.send_keys(email)
    await asyncio.sleep(1)

    # Password
    pw_input = None
    for attempt in range(4):
        pw_input = await page.select('input[name="password"]', timeout=5)
        if pw_input:
            break
        await asyncio.sleep(2)
    if not pw_input:
        return "Password input not found"
    await pw_input.click()
    await asyncio.sleep(0.5)
    await pw_input.send_keys(password)
    await asyncio.sleep(2)

    return None


async def _try_solve_turnstile(page: uc.Tab, quick: bool = False) -> str:
    """Try to solve Turnstile. Returns token or empty string."""
    turnstile_present = await is_turnstile_present(page)
    if not turnstile_present:
        return ""

    # Scroll to make Turnstile visible
    await page.evaluate("window.scrollBy(0, 400)")
    await asyncio.sleep(2)

    return await solve_turnstile(page, quick=quick)


async def _submit_form(page: uc.Tab) -> Optional[str]:
    """Click submit button. Returns error string or None."""
    submit_btn = await page.select('button[type="submit"]', timeout=5)
    if not submit_btn:
        return "Submit button not found"
    await submit_btn.scroll_into_view()
    await asyncio.sleep(1)
    await submit_btn.click()
    return None


async def _wait_for_redirect(page: uc.Tab, max_wait: int = 30) -> str:
    """Wait for signup to leave the sign-up page. Returns final URL."""
    for _ in range(max_wait):
        await asyncio.sleep(1)
        url = str(_unwrap(await page.evaluate("location.href")))
        if "/sign-up" not in url:
            return url
    return str(_unwrap(await page.evaluate("location.href")))


# Redirect targets that mean "signup accepted, account exists".
_ACCOUNT_CREATED_MARKERS = (
    "account/home",      # /?to=%2F%3Aaccount%2Fhome
    "%3aaccount%2fhome", # encoded form
    "/account/",         # any account-scoped page
    "/email-verification",
    "verify-email",
    "unintended-registration",
)


def _looks_account_created(url: str) -> bool:
    """True if the post-submit URL implies Cloudflare created the account."""
    low = url.lower()
    if "/sign-up" in low:
        return False
    return any(m in low for m in _ACCOUNT_CREATED_MARKERS)


async def _resolve_account_id(page: uc.Tab, url: str) -> str:
    """Try hard to resolve the 32-char account id from URL, DOM, then API."""
    # 1) URL
    match = re.search(r"/([a-f0-9]{32})(?:/|$|\?)", url)
    if match:
        return match.group(1)

    # 2) API: list accounts for the authenticated session.
    try:
        raw = await page.evaluate(
            """
            (async () => {
                try {
                    const r = await fetch('/api/v4/accounts?per_page=50', {
                        credentials: 'include',
                        headers: {'Accept': 'application/json'}
                    });
                    const text = await r.text();
                    return JSON.stringify({status: r.status, body: text});
                } catch (e) {
                    return JSON.stringify({status: 0, body: String(e)});
                }
            })()
            """,
            await_promise=True,
            return_by_value=True,
        )
        data = _unwrap(raw)
        if isinstance(data, str):
            import json as _json
            data = _json.loads(data)
        if isinstance(data, dict) and data.get("status") == 200:
            import json as _json
            body = _json.loads(data.get("body") or "{}")
            accounts = body.get("result") or []
            if accounts and accounts[0].get("id"):
                return accounts[0]["id"]
    except Exception:
        pass

    # 3) DOM
    try:
        account_id = _unwrap(await page.evaluate("""
            (() => {
                const el = document.querySelector('[data-account-id], [data-testid="account-id"]');
                return el ? (el.textContent || el.getAttribute('data-account-id')) : null;
            })()
        """))
        if account_id:
            return str(account_id).strip()
    except Exception:
        pass

    return ""


async def _extract_account_id(page: uc.Tab, url: str) -> Optional[str]:
    """Backward-compatible wrapper around _resolve_account_id."""
    return await _resolve_account_id(page, url) or None


async def _check_errors(page: uc.Tab) -> str:
    """Extract error messages from page."""
    error_msgs = _unwrap(await page.evaluate("""
        Array.from(document.querySelectorAll('p, [role="alert"], .error, .notification'))
            .map(e => (e.textContent || '').trim())
            .filter(t => t.length > 5 && (t.includes('unable') || t.includes('limit') ||
                t.includes('Incorrect') || t.includes('try again') || t.includes('blocked')))
    """))
    if error_msgs and isinstance(error_msgs, list):
        return "; ".join([str(_unwrap(m)) for m in error_msgs])
    return ""


async def signup(
    page: uc.Tab,
    email: str,
    password: str,
    max_wait: int = 30,
    retry_turnstile: bool = True,
) -> SignupResult:
    """
    Execute Cloudflare signup with submit-first strategy.

    1. Fill form
    2. Quick Turnstile interaction (non-blocking)
    3. Submit immediately
    4. If still on sign-up page → proper solve + resubmit
    5. Extract Account ID

    Args:
        page: nodriver Tab
        email: Email address
        password: Password
        max_wait: Max seconds to wait for redirect
        retry_turnstile: Whether to retry with proper solve on failure

    Returns:
        SignupResult with account_id on success
    """
    # ─── Navigate ───
    await page.get(CLOUDFLARE_SIGNUP_URL)
    await asyncio.sleep(8)

    # ─── Fill Form ───
    error = await _fill_form(page, email, password)
    if error:
        return SignupResult(False, email=email, error=error)

    # ─── Quick Turnstile (non-blocking) ───
    token = await _try_solve_turnstile(page, quick=True)
    if token:
        print(f"    ✅ Turnstile solved: {token[:20]}...")
    else:
        print(f"    ⚡ Turnstile quick-interact (submit-first)")

    # ─── Submit Form ───
    error = await _submit_form(page)
    if error:
        return SignupResult(False, email=email, error=error)

    # ─── Wait for Redirect ───
    url = await _wait_for_redirect(page, max_wait)
    await asyncio.sleep(5)

    # ─── Check Result ───
    account_id = await _resolve_account_id(page, url)

    if account_id:
        return SignupResult(
            True,
            email=email,
            password=password,
            account_id=account_id,
            page_url=url,
            account_created=True,
        )

    # Account was accepted by Cloudflare (redirected away from /sign-up) but the
    # 32-char id isn't resolvable until the email is verified. Treat as created.
    if _looks_account_created(url):
        print(f"    ✅ Account created (email verification pending); url={url[:70]}")
        return SignupResult(
            True,
            email=email,
            password=password,
            account_id="",
            page_url=url,
            account_created=True,
        )

    # ─── Still on sign-up page? Retry with proper solve ───
    if retry_turnstile and "/sign-up" in url:
        print(f"    🔄 Submit-first failed. Retry with full Turnstile solve...")

        # Reload and refill
        await page.get(CLOUDFLARE_SIGNUP_URL)
        await asyncio.sleep(5)

        error = await _fill_form(page, email, password)
        if error:
            return SignupResult(False, email=email, error=f"Retry fill failed: {error}")

        token = await _try_solve_turnstile(page, quick=False)
        print(f"    {'✅' if token else '⚠️ No token — forcing submit'} Turnstile retry")

        error = await _submit_form(page)
        if error:
            return SignupResult(False, email=email, error=f"Retry submit failed: {error}")

        url = await _wait_for_redirect(page, max_wait)
        await asyncio.sleep(5)

        account_id = await _resolve_account_id(page, url)
        if account_id or _looks_account_created(url):
            return SignupResult(
                True,
                email=email,
                password=password,
                account_id=account_id,
                page_url=url,
                account_created=True,
            )

    # ─── Final failure ───
    error_msgs = await _check_errors(page)
    error = error_msgs if error_msgs else f"Redirect failed: {url[:80]}"
    return SignupResult(False, email=email, error=error)
