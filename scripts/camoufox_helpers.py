"""
Camoufox helper utilities: proxy parsing, pool rotation, gateway sticky sessions,
and the shared Playwright page helpers (js/has/visible_first/type_into/...).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import random
import sys
import urllib.parse
from pathlib import Path
from typing import Optional
# Ensure scripts dir is on sys.path for gateway_session
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    from gateway_session import apply_gateway_session, is_gateway
except ImportError:
    def is_gateway(proxy_url: Optional[str] = None) -> bool:
        return False

    async def apply_gateway_session(page_or_tab, session_id: Optional[str] = None) -> bool:
        return False


def to_camoufox_proxy(proxy_url_or_dict: str | dict | None) -> dict | None:
    """Convert proxy URL string or dict to Camoufox/Playwright proxy dictionary.

    Format:
        {"server": "http://host:port", "username": "...", "password": "..."}
    """
    if not proxy_url_or_dict:
        return None
    if isinstance(proxy_url_or_dict, dict):
        return proxy_url_or_dict

    raw = str(proxy_url_or_dict).strip()
    if not raw or raw.lower() == "none" or raw.lower() == "direct":
        return None

    # Handle host:port:user:pass
    if "://" not in raw and raw.count(":") == 3:
        host, port, user, pw = raw.split(":", 3)
        return {
            "server": f"http://{host}:{port}",
            "username": user,
            "password": pw,
        }

    if "://" not in raw:
        raw = "http://" + raw

    u = urllib.parse.urlsplit(raw)
    server = f"{u.scheme}://{u.hostname}"
    if u.port:
        server += f":{u.port}"

    d: dict[str, str] = {"server": server}
    if u.username:
        d["username"] = urllib.parse.unquote(u.username)
    if u.password:
        d["password"] = urllib.parse.unquote(u.password)
    return d


def load_proxy_pool(pool_path: str | Path | None) -> list[str]:
    """Load proxy URLs from a text file, ignoring comments and blanks."""
    if not pool_path:
        return []
    p = Path(pool_path)
    if not p.exists():
        return []
    lines = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def pick_proxy(
    proxy: str | None = None,
    pool_path: str | Path | None = None,
    index: int = 0,
) -> str | None:
    """Return a single proxy URL from explicit arg or pool file round-robin."""
    if proxy:
        return proxy
    pool = load_proxy_pool(pool_path)
    if pool:
        return pool[index % len(pool)]
    return None


async def setup_camoufox_gateway(
    context,
    proxy_url: str | None,
    session_id: str | None = None,
) -> bool:
    """If proxy_url points to a local gateway, attach X-Session-ID for sticky sessions."""
    if not proxy_url or not is_gateway(proxy_url):
        return False
    sid = session_id or os.environ.get("GATEWAY_SESSION_ID") or os.environ.get("K12_GATEWAY_SESSION")
    if not sid:
        return False
    return await apply_gateway_session(context, sid)

# ─────────────────────────────────────────────────────────── playwright helpers

async def js(page, expr: str, default=None):
    """page.evaluate that never raises (navigation races) and returns a default."""
    try:
        r = await page.evaluate(expr)
    except Exception:
        return default
    return default if r is None else r

async def live_url(page) -> str:
    """Current URL (page.url is cached by Playwright; evaluate reads the live one)."""
    return await js(page, "location.href", "") or page.url or ""

async def has(page, selector: str) -> bool:
    try:
        return await page.locator(selector).count() > 0
    except Exception:
        return False

async def visible_first(page, selector: str):
    """First visible element matching selector (as a Locator), else None."""
    try:
        loc = page.locator(selector)
        for i in range(await loc.count()):
            el = loc.nth(i)
            if await el.is_visible():
                return el
    except Exception:
        pass
    return None

async def type_into(page, selector: str, value: str, label: str | None = None) -> bool:
    """Fill an input (React-safe) and VERIFY it holds the value.

    1. locator.fill()  -> clears + sets value with trusted input events
    2. fallback: click, Ctrl+A, Delete, press_sequentially (real per-key events)
    3. last resort: native value setter + InputEvent
    """
    el = await visible_first(page, selector) or page.locator(selector).first
    got = ""
    try:
        await el.click(timeout=4000)
        await el.fill(str(value), timeout=4000)
        got = await el.input_value(timeout=2000)
    except Exception:
        got = ""
    ok = got.strip() == str(value).strip()
    if not ok:
        try:
            await el.click(timeout=4000)
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Delete")
            await el.press_sequentially(str(value), delay=random.randint(45, 90), timeout=15000)
            got = await el.input_value(timeout=2000)
        except Exception:
            pass
        ok = got.strip() == str(value).strip()
    if not ok:
        try:
            await js(page, """(()=>{const e=document.querySelector(%s); if(!e) return '';
                e.focus();
                const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')?.set;
                if(s) s.call(e,%s); else e.value=%s;
                e.dispatchEvent(new InputEvent('input',{bubbles:true,data:%s,inputType:'insertText'}));
                e.dispatchEvent(new Event('change',{bubbles:true}));
                return e.value;})()""" % (json.dumps(selector), json.dumps(str(value)),
                                          json.dumps(str(value)), json.dumps(str(value))), "")
            try:
                got = await el.input_value(timeout=2000)
            except Exception:
                got = await js(page, f"(()=>{{const e=document.querySelector({json.dumps(selector)});return e?e.value:'';}})()", "") or ""
        except Exception:
            pass
        ok = got.strip() == str(value).strip()
    shown = "*" * len(got) if "password" in selector else got
    print(f"      [type] {label or selector} <- {'*' * len(value) if 'password' in selector else value!r} "
          f"=> {shown!r} ok={ok}", flush=True)
    return ok

async def fill_first_input(page, selectors: list[str], value: str) -> bool:
    for sel in selectors:
        if await visible_first(page, sel) is not None and await type_into(page, sel, value):
            return True
    # fallback: first visible plain text input
    loc = page.locator("input:not([type=hidden]):not([type=submit]):not([type=checkbox])"
                       ":not([type=radio]):not([type=file]):not([type=search])")
    try:
        for i in range(await loc.count()):
            el = loc.nth(i)
            if await el.is_visible():
                await el.click(timeout=4000)
                await el.fill(value, timeout=4000)
                return (await el.input_value()).strip() == value.strip()
    except Exception:
        pass
    return False


async def detect_challenge(page) -> str | None:
    """Detect bot detection or interactive security challenges on the current page.

    Unifies markers across DataDome, Arkose/FunCaptcha, hCaptcha, reCAPTCHA,
    Cloudflare/Turnstile, 2FA/OTP, and waiting-room interstitials.
    """
    try:
        url = getattr(page, "url", "") or ""
        if callable(url):
            url = url()
        url = str(url).lower()

        title = ""
        if hasattr(page, "title"):
            try:
                t = page.title()
                if inspect.isawaitable(t):
                    title = ((await t) or "").lower()
                else:
                    title = (str(t) or "").lower()
            except Exception:
                pass
        # 1. Cloudflare / DataDome / 2FA in URL
        if "cf-challenge" in url or "turnstile" in url:
            return "Cloudflare Turnstile / Challenge detected in URL"
        if "datadome" in url:
            return "DataDome challenge detected in URL"
        if "/sessions/two-factor" in url:
            return "GitHub Two-Factor Authentication (2FA) prompt required"
        if "/sessions/verified-device" in url:
            return "GitHub Device Verification email prompt required"

        # 2. Page title markers (Cloudflare / waiting room)
        if "just a moment..." in title or "attention required" in title:
            return "Cloudflare waiting room / challenge detected in page title"
        if "waiting room" in title:
            return "Cloudflare waiting room / challenge detected in page title"

        # 3. Locators: Cloudflare, Arkose, 2FA, hCaptcha, reCAPTCHA
        if hasattr(page, "locator"):
            try:
                if await page.locator("iframe[src*='challenges.cloudflare.com']").count() > 0:
                    return "Cloudflare Turnstile iframe present"
            except Exception:
                pass

            try:
                if await page.locator("iframe[src*='arkose'], #octocaptcha, iframe[src*='funcaptcha']").count() > 0:
                    return "Arkose Labs / Octocaptcha challenge present"
            except Exception:
                pass

            try:
                if await page.locator("input[name='otp'], #app_totp, #sms_totp").count() > 0:
                    return "GitHub OTP/2FA input field detected"
            except Exception:
                pass

            try:
                if await page.locator("iframe[src*='hcaptcha'], .h-captcha").count() > 0:
                    return "hCaptcha"
            except Exception:
                pass

            try:
                if await page.locator("iframe[src*='recaptcha'], .g-recaptcha").count() > 0:
                    return "reCAPTCHA"
            except Exception:
                pass

        # 4. DOM / HTML string markers
        html = await js(page, "document.documentElement ? document.documentElement.outerHTML : ''", "")
        low = (html or "").lower()
        if low:
            for needle, label in (
                ("arkoselabs", "Arkose / FunCaptcha"),
                ("funcaptcha", "Arkose / FunCaptcha"),
                ("hcaptcha", "hCaptcha"),
                ("recaptcha", "reCAPTCHA"),
                ("datadome", "DataDome challenge detected in page"),
                ("cf-chl", "Cloudflare challenge present"),
                ("challenge-platform", "Cloudflare challenge present"),
                ("challenge-container", "GitHub challenge iframe"),
                ("verify you are human", "human-verification prompt"),
                ("enable javascript", "GitHub anti-bot page (enable JavaScript)"),
                ("disable your ad blocker", "GitHub anti-bot page (disable adblocker)"),
                ("disable adblocker", "GitHub anti-bot page (disable adblocker)"),
                ("unusual activity", "GitHub anti-bot page (unusual activity)"),
            ):
                if needle in low:
                    return label

        # 5. Visible body text markers
        txt = (await js(page, "document.body ? document.body.innerText : ''", "") or "").lower()
        if "verify" in txt and "human" in txt:
            return "human-verification prompt (text)"
        if "waiting room" in txt:
            return "waiting room detected in page"

    except Exception:
        pass

    return None
