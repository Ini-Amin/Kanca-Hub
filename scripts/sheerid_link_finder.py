#!/usr/bin/env python3
"""
Automated SheerID verification link finder from school mailbox via CAMOUFOX (Firefox anti-detect).

Searches Outlook Web (binus.ac.id / M365) for SheerID verification messages,
extracts and unwraps the real SheerID verification URL (handling Microsoft Defender
SafeLinks), and optionally navigates to it to proceed with verification.

Features:
  - Camoufox Firefox anti-detect engine with geoip, humanize, and Windows fingerprinting
  - Persistent browser profile (~/.config/auto-freecf/camoufox-school) so M365 session survives
  - Interactive first-run login mode (--login) for initial MFA / session establishment
  - Local PetaniProxy gateway support (--proxy, --pool) with X-Session-ID sticky sessions
  - Microsoft Defender SafeLinks unwrapping (apc01.safelinks.protection.outlook.com)
  - Token extraction (verificationId / emailToken) and URL synthesis

Usage:
    # First time: log into school Microsoft account interactively to persist session
    python3 scripts/sheerid_link_finder.py --login

    # Find and extract newest SheerID verification link:
    python3 scripts/sheerid_link_finder.py

    # Find link and navigate to it (proceed with verification):
    python3 scripts/sheerid_link_finder.py --open

    # JSON output for automated pipelines:
    python3 scripts/sheerid_link_finder.py --json

    # Via PetaniProxy local gateway:
    python3 scripts/sheerid_link_finder.py --proxy http://127.0.0.1:8888
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Optional

# Auto-re-exec into isolated camoufox-venv if camoufox is not installed in current interpreter
CAMOUFOX_VENV_PY = Path.home() / ".local" / "share" / "auto-freecf" / "camoufox-venv" / "bin" / "python"
if CAMOUFOX_VENV_PY.exists() and sys.executable != str(CAMOUFOX_VENV_PY):
    try:
        import camoufox  # noqa: F401
    except ImportError:
        os.execv(str(CAMOUFOX_VENV_PY), [str(CAMOUFOX_VENV_PY)] + sys.argv)

from camoufox.async_api import AsyncCamoufox

# Ensure scripts dir is on sys.path
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    from camoufox_helpers import pick_proxy, setup_camoufox_gateway, to_camoufox_proxy
except ImportError:
    def to_camoufox_proxy(p): return None
    def pick_proxy(proxy=None, pool_path=None, index=0): return proxy
    async def setup_camoufox_gateway(ctx, p, sid=None): return False

DEFAULT_PROFILE_DIR = Path.home() / ".config" / "auto-freecf" / "camoufox-school"
MAIL_URL = "https://outlook.office.com/mail/"


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    p = Path.home() / ".config" / "auto-freecf" / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


_ENV = _load_env()


def cfg(key: str, default: str = "") -> str:
    return os.environ.get(key) or _ENV.get(key, default)


def unwrap_safelink(url: str) -> str:
    """Unwrap Microsoft Defender SafeLinks URL to obtain real target URL."""
    if "safelinks.protection.outlook.com" in url:
        try:
            parsed = urllib.parse.urlparse(url)
            qs = urllib.parse.parse_qs(parsed.query)
            if "url" in qs and qs["url"]:
                return qs["url"][0]
        except Exception:
            pass
    return url


def extract_sheerid_link(content: str) -> tuple[Optional[str], Optional[str]]:
    """Extract and unwrap SheerID verification link and token from HTML or text.

    Returns:
        (verification_url, token)
    """
    if not content:
        return None, None

    # 1. Search full SheerID URL patterns
    patterns = [
        r'https://services\.sheerid\.com/verify/[^\s<>"\'\\]+',
        r'https://verify\.sheerid\.com/verify/[^\s<>"\'\\]+',
        r'https://[a-zA-Z0-9.-]*\.safelinks\.protection\.outlook\.com/\?[^\s<>"\'\\]+',
        r'https://[^\s<>"\'\\]*sheerid[^\s<>"\'\\]*verify[^\s<>"\'\\]*',
    ]

    for p in patterns:
        matches = re.findall(p, content, re.IGNORECASE)
        for m in matches:
            u = unwrap_safelink(m)
            u = u.replace("&amp;", "&").replace("\\/", "/").rstrip(".,;)>]\"'")
            if "services.sheerid.com/verify" in u or "verify.sheerid.com" in u:
                tok = None
                m_tok = re.search(r"(?:verificationId|emailToken|token)=([a-zA-Z0-9]+)", u, re.IGNORECASE)
                if m_tok:
                    tok = m_tok.group(1)
                return u, tok

    # 2. Token-only fallback (construct URL using default program template)
    token_patterns = [
        (r'emailToken=([a-zA-Z0-9]+)', "emailToken"),
        (r'verificationId=([a-f0-9]+)', "verificationId"),
        (r'token=([a-zA-Z0-9]+)', "emailToken"),
    ]
    for pat, param in token_patterns:
        m_tok = re.search(pat, content, re.IGNORECASE)
        if m_tok:
            tok = m_tok.group(1)
            constructed = f"https://services.sheerid.com/verify/68d47554aa292d20b9bec8f7/?{param}={tok}"
            return constructed, tok

    return None, None


async def do_login(page: Any, email: str, password: str) -> bool:
    """Walk Microsoft login flow in Camoufox."""
    print("      [school] completing Microsoft login in Camoufox…", flush=True)
    try:
        # 1. Email step
        email_input = page.locator('input[type=email], input[name=loginfmt]').first
        await email_input.wait_for(timeout=20000)
        await email_input.fill(email)
        await asyncio.sleep(0.5)

        next_btn = page.locator('input[type=submit], button[type=submit], button:has-text("Next")').first
        await next_btn.click(timeout=5000)
        await asyncio.sleep(4)

        # 2. Password step
        pw_input = page.locator('input[type=password], input[name=passwd]').first
        await pw_input.wait_for(timeout=20000)
        await pw_input.fill(password)
        await asyncio.sleep(0.5)

        submit_btn = page.locator('input[type=submit], button[type=submit], button:has-text("Sign in")').first
        await submit_btn.click(timeout=5000)
        await asyncio.sleep(5)

        # 3. Stay signed in?
        stay_btn = page.locator('input[type=submit][value="Yes"], button:has-text("Yes"), input[type=submit][value="No"]').first
        if await stay_btn.count() > 0 and await stay_btn.is_visible():
            await stay_btn.click(timeout=5000)
            print("      [school] answered 'Stay signed in'", flush=True)

        await asyncio.sleep(5)
        return True
    except Exception as e:
        print(f"      [school] login helper notice: {e}", flush=True)
        return False


async def wait_inbox(page: Any, timeout: float = 60.0) -> bool:
    """Wait until Outlook Web inbox view is ready."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        u = page.url or ""
        if "outlook" in u and ("mail" in u or "owa" in u):
            # Check for standard inbox indicators
            loc = page.locator('[role=main], [aria-label*="Message" i], div[role=listbox], div.customScrollBar')
            if await loc.count() > 0:
                return True
        await asyncio.sleep(1)
    return False


async def scan_and_extract_link(page: Any) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Scan visible SheerID messages, open reading pane, and extract link."""
    # Find message items with SheerID or verification in aria-label
    rows = page.locator('[aria-label*="SheerID" i], [aria-label*="verification" i]')
    count = await rows.count()
    if count == 0:
        return None, None, None

    # Collect row metadata and prioritize action links over general support replies
    items = []
    for i in range(count):
        row = rows.nth(i)
        aria = await row.get_attribute("aria-label") or ""
        priority = 2
        if any(k in aria.lower() for k in ("finish verifying", "get verified", "verify your")):
            priority = 0
        elif "verification" in aria.lower():
            priority = 1
        items.append((priority, i, aria))

    items.sort(key=lambda x: x[0])

    for _, idx, aria in items:
        try:
            target_row = rows.nth(idx)
            await target_row.click(timeout=4000)
            await asyncio.sleep(3.5)

            # 1. Check all anchor tags inside reading pane / page
            links = await page.locator('a[href]').all()
            for link_el in links:
                try:
                    href = await link_el.get_attribute("href") or ""
                    if any(k in href.lower() for k in ("sheerid", "safelinks", "verificationid", "emailtoken")):
                        u, tok = extract_sheerid_link(href)
                        if u:
                            return u, tok, aria
                except Exception:
                    continue

            # 2. Check innerHTML of reading pane
            pane = page.locator('[role=main], [aria-label*="Reading Pane" i], div.ItemPartView').first
            if await pane.count() > 0:
                html = await pane.inner_html()
                u, tok = extract_sheerid_link(html)
                if u:
                    return u, tok, aria
        except Exception:
            continue

    return None, None, None


async def check_resulting_page(page: Any) -> dict[str, str]:
    """Inspect page state after navigating to SheerID verification link."""
    await asyncio.sleep(5)
    try:
        title = await page.title()
        url = page.url
        text = await page.evaluate("() => document.body ? document.body.innerText.slice(0, 300) : ''")
        status = "loaded"
        if re.search(r'verified|approved|success|congratulations', text, re.I):
            status = "verified"
        elif re.search(r'upload|document|documentation', text, re.I):
            status = "document_upload_required"
        elif re.search(r'pending|review', text, re.I):
            status = "under_review"
        elif re.search(r'expired|invalid|reached the max', text, re.I):
            status = "expired_or_invalid"

        return {
            "status": status,
            "title": title,
            "url": url,
            "snippet": text.replace("\n", " ").strip(),
        }
    except Exception as e:
        return {"status": "unknown", "error": str(e)}


async def run_sheerid_finder(
    timeout: int = 180,
    open_link: bool = False,
    headless: bool = False,
    login_mode: bool = False,
    proxy_url: Optional[str] = None,
    profile_dir: Path = DEFAULT_PROFILE_DIR,
) -> dict[str, Any]:
    """Execute SheerID link search in Camoufox."""
    email = cfg("SCHOOL_EMAIL")
    pw = cfg("SCHOOL_MAIL_PASSWORD")
    url = cfg("SCHOOL_MAIL_URL", MAIL_URL)

    profile_dir.mkdir(parents=True, exist_ok=True)
    proxy_dict = to_camoufox_proxy(proxy_url)

    launch_kw: dict[str, Any] = {
        "headless": "virtual" if headless else False,
        "geoip": True,
        "humanize": True,
        "os": "windows",
        "persistent_context": True,
        "user_data_dir": str(profile_dir),
    }
    if proxy_dict:
        launch_kw["proxy"] = proxy_dict

    print(f"      [camoufox] starting Camoufox (profile={profile_dir})…", flush=True)

    result: dict[str, Any] = {
        "success": False,
        "url": None,
        "token": None,
        "source_subject": None,
    }

    async with AsyncCamoufox(**launch_kw) as context:
        # With persistent_context=True, __aenter__ returns BrowserContext
        page = context.pages[0] if context.pages else await context.new_page()

        # Setup sticky session if running through local gateway
        if proxy_url:
            sid = email or "school-mailbox"
            await setup_camoufox_gateway(context, proxy_url, sid)

        print(f"      [camoufox] opening {url}…", flush=True)
        await page.goto(url, wait_until="domcontentloaded")
        await asyncio.sleep(5)

        # Handle interactive login mode
        if login_mode:
            print("\n" + "=" * 65)
            print("🔑 INTERACTIVE LOGIN MODE (Camoufox Persistent Profile)")
            print(f"   Profile directory: {profile_dir}")
            print("   Please complete your Microsoft / BINUS login and MFA.")
            print("   Press Enter in this terminal when your inbox is open.")
            print("=" * 65 + "\n", flush=True)

            if email and pw and ("login" in page.url.lower() or "sign in" in (await page.title()).lower()):
                await do_login(page, email, pw)

            # Wait for user confirmation in interactive mode
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, input, "Press Enter after you see your Outlook Inbox: ")
            except (EOFError, KeyboardInterrupt):
                pass

            ready = await wait_inbox(page, timeout=15)
            print(f"      [school] inbox ready: {ready}")
            print(f"      [school] session saved to {profile_dir}")
            result["success"] = ready
            result["message"] = "Login completed and session persisted"
            return result

        # Normal automated run
        curr_url = page.url or ""
        curr_title = await page.title()
        if any(k in curr_url for k in ("login.microsoftonline", "login.live", "adfs")) or "sign in" in curr_title.lower():
            if pw and email:
                await do_login(page, email, pw)
            else:
                result["error"] = ("Session not logged in. Run with '--login' first to establish your "
                                   "persistent Camoufox profile.")
                return result

        ready = await wait_inbox(page, timeout=45)
        if not ready:
            result["error"] = f"Inbox not ready within timeout (url={page.url})"
            return result

        print("      [school] inbox loaded; scanning for SheerID verification messages…", flush=True)
        deadline = time.time() + timeout
        while time.time() < deadline:
            link, tok, aria = await scan_and_extract_link(page)
            if link:
                result["success"] = True
                result["url"] = link
                result["token"] = tok
                result["source_subject"] = aria
                break

            await asyncio.sleep(5)

        if not result["success"]:
            result["error"] = f"No SheerID verification link found after {timeout}s"
            return result

        # Optional: open verification link and report destination state
        if open_link and result.get("url"):
            target_link = result["url"]
            print(f"      [sheerid] navigating to verification link: {target_link[:80]}…", flush=True)
            await page.goto(target_link, wait_until="domcontentloaded")
            state = await check_resulting_page(page)
            result["page_state"] = state
            await asyncio.sleep(2)

        return result


def main():
    ap = argparse.ArgumentParser(description="Automated SheerID verification link finder via Camoufox")
    ap.add_argument("--timeout", type=int, default=180, help="polling timeout in seconds (default: 180)")
    ap.add_argument("--open", action="store_true", help="navigate to the link and inspect resulting page")
    ap.add_argument("--json", action="store_true", help="output result in JSON format")
    ap.add_argument("--login", action="store_true", help="interactive mode to complete first-time login")
    ap.add_argument("--headless", action="store_true", help="run browser in virtual headless mode (Xvfb)")
    ap.add_argument("--proxy", default=None, help="proxy URL (e.g. http://127.0.0.1:8888)")
    ap.add_argument("--pool", default=None, help="proxy pool file path")
    ap.add_argument("--profile-dir", default=str(DEFAULT_PROFILE_DIR), help="Camoufox persistent profile path")
    args = ap.parse_args()

    effective_proxy = pick_proxy(proxy=args.proxy, pool_path=args.pool)

    res = asyncio.run(run_sheerid_finder(
        timeout=args.timeout,
        open_link=args.open,
        headless=args.headless,
        login_mode=args.login,
        proxy_url=effective_proxy,
        profile_dir=Path(args.profile_dir),
    ))

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        if res.get("success"):
            print("\n" + "=" * 60)
            print("✅ SheerID Verification Link Found:")
            print(f"   URL     : {res.get('url')}")
            print(f"   Token   : {res.get('token')}")
            print(f"   Subject : {res.get('source_subject', '')[:90]}")
            if res.get("page_state"):
                ps = res["page_state"]
                print(f"   Status  : {ps.get('status')} ({ps.get('title')})")
                if ps.get("snippet"):
                    print(f"   Snippet : {ps.get('snippet')[:120]}…")
            if res.get("message"):
                print(f"   Note    : {res.get('message')}")
            print("=" * 60 + "\n")
        else:
            print(f"\n❌ Error: {res.get('error')}\n", file=sys.stderr)

    sys.exit(0 if res.get("success") else 1)


if __name__ == "__main__":
    main()
