#!/usr/bin/env python3
"""
Automated SheerID verification link finder for school mailboxes (nodriver).

When SheerID verifies by email ('emailLoop'), it sends a verification link to
the school mailbox:
    https://services.sheerid.com/verify/<programId>/?verificationId=<token>
    (or ?emailToken=<token>)

In Outlook Web (BINUS and M365 tenants), these links are often rewritten by
Microsoft Defender SafeLinks (https://*.safelinks.protection.outlook.com/?url=...).

This tool:
1. Opens Outlook Web with the persisted school profile (~/.config/auto-freecf/school-profile).
2. Scans/searches for SheerID verification messages in the mailbox.
3. Opens the newest SheerID message, extracts and unwraps the real SheerID verification link.
4. Optionally (--open) navigates to the verification link and reports the resulting page state.

Usage:
    python3 scripts/sheerid_link_finder.py                     # Find and print link
    python3 scripts/sheerid_link_finder.py --open              # Find, print, and navigate
    python3 scripts/sheerid_link_finder.py --json              # Output JSON format
    python3 scripts/sheerid_link_finder.py --timeout 120       # Custom timeout
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
from typing import Optional

import nodriver as uc

PROFILE_DIR = Path.home() / ".config" / "auto-freecf" / "school-profile"
MAIL_URL = "https://outlook.office.com/mail/"


def _load_env() -> dict:
    env = {}
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


async def _js(tab, expr: str, default=None):
    try:
        r = await tab.evaluate(expr, return_by_value=True)
        if r is not None and r.__class__.__name__ == "RemoteObject":
            r = getattr(r, "value", None)
        if isinstance(r, dict) and "value" in r:
            r = r["value"]
        return default if r is None else r
    except Exception:
        return default


async def _maybe_click(tab, text: str, timeout: float = 3.0) -> bool:
    try:
        el = await tab.find(text, best_match=True, timeout=timeout)
        if el:
            await el.click()
            return True
    except Exception:
        pass
    return False


async def do_login(tab, email: str, password: str) -> None:
    """Walk Microsoft login flow if profile session expired."""
    print("      [school] completing Microsoft login…", flush=True)
    # 1. Email input
    for _ in range(20):
        if await _js(tab, "!!document.querySelector('input[type=email],input[name=loginfmt]')", False):
            break
        await asyncio.sleep(1)
    await _js(tab, """(()=>{const e=document.querySelector('input[type=email],input[name=loginfmt]');
        if(!e) return 0; e.focus();
        const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
        s.call(e,%s); e.dispatchEvent(new InputEvent('input',{bubbles:true,data:%s}));
        return 1;})()""" % (json.dumps(email), json.dumps(email)), 0)
    await asyncio.sleep(0.5)
    await _maybe_click(tab, "Next")
    await asyncio.sleep(4)

    # 2. Password input
    for _ in range(20):
        if await _js(tab, "!!document.querySelector('input[type=password],input[name=passwd]')", False):
            break
        await asyncio.sleep(1)
    await _js(tab, """(()=>{const e=document.querySelector('input[type=password],input[name=passwd]');
        if(!e) return 0; e.focus();
        const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
        s.call(e,%s); e.dispatchEvent(new InputEvent('input',{bubbles:true,data:%s}));
        return 1;})()""" % (json.dumps(password), json.dumps(password)), 0)
    await asyncio.sleep(0.5)
    await _maybe_click(tab, "Sign in")
    await asyncio.sleep(6)

    # 3. Stay signed in
    for label in ("Yes", "No"):
        if await _maybe_click(tab, label, timeout=4):
            print(f"      [school] answered '{label}' to stay-signed-in", flush=True)
            break
    await asyncio.sleep(6)


async def wait_inbox(tab, timeout: float = 60) -> bool:
    """Wait until Outlook Web inbox view is ready."""
    for _ in range(int(timeout)):
        u = await _js(tab, "location.href", "")
        if "outlook" in u and ("mail" in u or "owa" in u):
            if await _js(tab, "!!document.querySelector('[role=main],[aria-label*=Message],div[role=list]')", False):
                return True
        await asyncio.sleep(1)
    return False


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
            # Default K12 SheerID program ID template
            constructed = f"https://services.sheerid.com/verify/68d47554aa292d20b9bec8f7/?{param}={tok}"
            return constructed, tok

    return None, None


async def get_sheerid_message_items(tab) -> list[dict]:
    """Find visible SheerID message items in the conversation list."""
    js_code = """
    (() => {
        const rows = [];
        const cands = document.querySelectorAll('[aria-label*="SheerID" i], [aria-label*="verification" i]');
        for (let i = 0; i < cands.length; i++) {
            const el = cands[i];
            const aria = (el.getAttribute('aria-label') || '').trim();
            if (aria.toLowerCase().includes('sheerid')) {
                rows.push({
                    index: i,
                    aria: aria.replace(/\\n/g, ' ').slice(0, 250),
                    text: (el.innerText || '').replace(/\\n/g, ' ').slice(0, 150)
                });
            }
        }
        return JSON.stringify(rows);
    })()
    """
    raw = await _js(tab, js_code, "[]")
    try:
        return json.loads(raw) if isinstance(raw, str) else (raw or [])
    except Exception:
        return []


async def open_message_and_extract(tab, item_index: int) -> tuple[Optional[str], Optional[str], str]:
    """Click a message row in Outlook Web and extract any SheerID link in the reading pane."""
    click_code = f"""
    (() => {{
        const cands = document.querySelectorAll('[aria-label*="SheerID" i], [aria-label*="verification" i]');
        const target = cands[{item_index}];
        if (target) {{
            target.click();
            return target.getAttribute('aria-label') || '';
        }}
        return '';
    }})()
    """
    aria = await _js(tab, click_code, "")
    await asyncio.sleep(3.5)

    # Scrape links and reading pane HTML
    read_code = """
    (() => {
        const hrefs = [];
        for (const a of document.querySelectorAll('a[href]')) {
            const h = a.href || '';
            if (h.includes('sheerid') || h.includes('safelinks') || h.includes('verificationId') || h.includes('emailToken')) {
                hrefs.push(h);
            }
        }
        const pane = document.querySelector('[role=main], [aria-label*="Reading Pane" i], div.ItemPartView') || document.body;
        return JSON.stringify({
            hrefs: hrefs,
            html: pane ? pane.innerHTML : ''
        });
    })()
    """
    raw = await _js(tab, read_code, "{}")
    try:
        data = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        data = {}

    # Check extracted hrefs first
    for href in data.get("hrefs", []):
        u, tok = extract_sheerid_link(href)
        if u:
            return u, tok, str(aria)

    # Check reading pane HTML body
    u, tok = extract_sheerid_link(data.get("html", ""))
    return u, tok, str(aria)


async def check_resulting_page(tab) -> dict:
    """Inspect the page state after navigating to the SheerID verification link."""
    await asyncio.sleep(5)
    js_code = """
    (() => {
        const text = (document.body ? document.body.innerText : '') || '';
        const title = document.title || '';
        const u = location.href || '';
        let status = 'loaded';
        if (/verified|approved|success|congratulations/i.test(text)) status = 'verified';
        else if (/upload|document|documentation/i.test(text)) status = 'document_upload_required';
        else if (/pending|review/i.test(text)) status = 'under_review';
        else if (/expired|invalid|reached the max/i.test(text)) status = 'expired_or_invalid';
        return JSON.stringify({
            status: status,
            title: title,
            url: u,
            snippet: text.slice(0, 200).replace(/\\n/g, ' ')
        });
    })()
    """
    raw = await _js(tab, js_code, "{}")
    try:
        return json.loads(raw) if isinstance(raw, str) else {"status": "unknown"}
    except Exception:
        return {"status": "unknown"}


async def find_sheerid_link(
    timeout: int = 180,
    open_link: bool = False,
    headless: bool = False,
) -> dict:
    """Main flow: open Outlook Web, search for SheerID message, extract and optionally open."""
    email = cfg("SCHOOL_EMAIL")
    pw = cfg("SCHOOL_MAIL_PASSWORD")
    url = cfg("SCHOOL_MAIL_URL", MAIL_URL)

    if not email:
        return {"success": False, "error": "SCHOOL_EMAIL not configured in ~/.config/auto-freecf/.env"}

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    browser = await uc.start(headless=headless, sandbox=False, user_data_dir=str(PROFILE_DIR))
    result = {"success": False, "url": None, "token": None, "source_subject": None}

    try:
        tab = await browser.get(url)
        await asyncio.sleep(6)

        # Login handler if session expired
        cur = await _js(tab, "location.href", "")
        title = await _js(tab, "document.title", "")
        if any(k in cur for k in ("login.microsoftonline", "login.live", "adfs")) or "sign in" in (title or "").lower():
            if pw:
                await do_login(tab, email, pw)
            else:
                result["error"] = "Session expired and SCHOOL_MAIL_PASSWORD not set"
                return result

        ready = await wait_inbox(tab, timeout=45)
        if not ready:
            result["error"] = f"Inbox not ready within timeout (url={await _js(tab, 'location.href', '')})"
            return result

        deadline = time.time() + timeout
        poll_count = 0

        while time.time() < deadline:
            poll_count += 1
            items = await get_sheerid_message_items(tab)

            # Sort items to prioritize active verification / action messages over support replies
            items.sort(
                key=lambda x: (
                    0 if any(k in x.get("aria", "").lower() for k in ("finish verifying", "get verified", "verify your"))
                    else (1 if "verification" in x.get("aria", "").lower() else 2)
                )
            )

            for item in items:
                link, tok, aria = await open_message_and_extract(tab, item["index"])
                if link:
                    result["success"] = True
                    result["url"] = link
                    result["token"] = tok
                    result["source_subject"] = aria
                    break

            if result["success"]:
                break

            await asyncio.sleep(5)

        if not result["success"]:
            result["error"] = f"No SheerID verification link found after {timeout}s"
            return result

        # Optional: open verification link and report destination state
        if open_link and result.get("url"):
            print(f"      [sheerid] navigating to verification link: {result['url'][:80]}…", flush=True)
            await tab.get(result["url"])
            state = await check_resulting_page(tab)
            result["page_state"] = state
            await asyncio.sleep(4)

        return result
    finally:
        browser.stop()


def main():
    ap = argparse.ArgumentParser(description="Automated SheerID verification link finder from school mailbox")
    ap.add_argument("--timeout", type=int, default=180, help="polling timeout in seconds (default: 180)")
    ap.add_argument("--open", action="store_true", help="navigate to the link and inspect resulting page")
    ap.add_argument("--json", action="store_true", help="output result in JSON format")
    ap.add_argument("--headless", action="store_true", help="run browser in headless mode")
    args = ap.parse_args()

    res = asyncio.run(find_sheerid_link(timeout=args.timeout, open_link=args.open, headless=args.headless))

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
            print("=" * 60 + "\n")
        else:
            print(f"\n❌ Error: {res.get('error')}\n", file=sys.stderr)

    sys.exit(0 if res.get("success") else 1)


if __name__ == "__main__":
    main()
