#!/usr/bin/env python3
"""
GitHub signup + GitHub Education (Student Pack) application — **Camoufox**, Linux.

Ported from the nodriver/CDP version to Camoufox (anti-detect Firefox via the
Playwright API), matching scripts/k12_camoufox.py. Firefox/Juggler input is
trusted, so the old CDP click/insertText helpers are replaced with
locator.click / fill / press_sequentially / expect_popup, and
AsyncCamoufox(geoip=True, humanize=True, os="windows") makes timezone/locale/geo
and WebRTC follow the exit IP.

MUST run under the isolated Camoufox venv (python3.11 + playwright):
    /home/amen/.local/share/auto-freecf/camoufox-venv/bin/python \
        /home/amen/Auto-FreeCF/scripts/github_farm.py --index 1 --dry-run

WHAT THIS AUTOMATES (honestly)
==============================
  ✅ NEW GitHub account signup via https://github.com/signup
       - email entry  (plus-address: raymondi+gh<N>@binus.ac.id)
       - password entry
       - unique username generation + availability retry
       - the "verify your email" **8-digit launch code** step, read live from the
         BINUS M365 school mailbox (Outlook Web) using the same browser-reader
         approach as scripts/school_mail_browser.py / sheerid_link_finder.py
       - account-created detection
  ✅ Opens the GitHub Education application form and FILLS the fields we can:
       - school = BINUS University / Universitas Bina Nusantara
       - school email = the same plus-address
       - name, and (best-effort) academic fields that are present
  ✅ Saves the created account (email/username/password) to
     ~/Auto-FreeCF/github_accounts.json

WHAT THIS CANNOT AUTOMATE (do NOT overclaim)
============================================
  ❌ CAPTCHAs / Arkose "verify you are human" puzzle. GitHub increasingly gates
     signup behind Arkose + device fingerprint checks. Camoufox reduces bot
     signals but there is NO solver here. If a puzzle appears we DETECT it and
     REPORT it — we never claim success.
  ❌ Network/IP blocks: GitHub may serve an 'Access is temporarily restricted'
     interstitial (DataDome-style anti-bot) for a flagged egress IP. Detected
     and reported; retry from another IP (--proxy / --pool).
  ❌ Identity / academic attestation on the Education form:
       - legal name attestation, "I am a student" checkbox, and especially the
         **photo/scan of a student ID or enrollment proof** that a human must
         provide and that GitHub reviews manually.
     We stop BEFORE anything requiring an attestation or a photo upload.
  ❌ Email domain proof for the student pack, billing, and final human review.
  ❌ MFA / device verification on the GitHub account if GitHub enforces it.

Because of the above, treat this as a *helper*, not a turnkey farmer. Expect to
finish captchas and the Education photo step by hand.

PROXY / POOL
============
  --proxy URL   single egress. Supports the local KancaHub rotating gateway
                http://127.0.0.1:8888 (or :8899): when detected, a sticky
                X-Session-ID header is applied via scripts/gateway_session.py so
                one upstream proxy is pinned for the whole flow.
  --pool FILE   newline-separated proxy list; one is chosen per --index (rotation).
                Auto-detects the gateway and applies sticky sessions for it too.
  Exit IP / geo follow the proxy when geoip=True.

CONFIG (~/.config/auto-freecf/.env) — same keys the school reader uses:
    SCHOOL_EMAIL=raymondi@binus.ac.id
    SCHOOL_MAIL_PASSWORD=...
    SCHOOL_MAIL_URL=https://outlook.office.com/mail/   # optional

CLI:
    python3 github_farm.py --check                 # deps + mailbox self-report
    python3 github_farm.py --index 1 --dry-run     # walk signup, screenshot, no submit
    python3 github_farm.py --index 1               # real signup (stops before captcha/attestation)
    python3 github_farm.py --index 1 --headless
    python3 github_farm.py --index 1 --proxy http://127.0.0.1:8888
    python3 github_farm.py --index 1 --pool signup_from_scratch/proxies.txt

Output: ~/Auto-FreeCF/github_accounts.json (gitignored).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import string
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

try:
    from camoufox.async_api import AsyncCamoufox
except Exception as _e:  # pragma: no cover - only triggers outside the camoufox venv
    AsyncCamoufox = None
    _CAMOUFOX_IMPORT_ERROR = _e
else:
    _CAMOUFOX_IMPORT_ERROR = None

try:
    from playwright.async_api import TimeoutError as PWTimeout
    from playwright.async_api import Error as PWError
except Exception:  # pragma: no cover
    class PWTimeout(Exception):  # type: ignore
        ...
    class PWError(Exception):  # type: ignore
        ...

try:
    from camoufox.ip import Proxy as CamoufoxProxy
except Exception:  # pragma: no cover
    CamoufoxProxy = None  # type: ignore

# Sticky-session helper for the local KancaHub gateway (defensive import).
try:
    from gateway_session import apply_gateway_session, is_gateway
except ImportError:
    try:
        from scripts.gateway_session import apply_gateway_session, is_gateway
    except Exception:
        def is_gateway(p=None):  # type: ignore
            return False
        async def apply_gateway_session(page_or_ctx, s=None):  # type: ignore
            return False

# Proxy & clean egress helpers (defensive import)
try:
    from proxy_lib import check_gateway_egress, ensure_clean_egress, get_my_ip, stop_gateway
except ImportError:
    try:
        from scripts.proxy_lib import check_gateway_egress, ensure_clean_egress, get_my_ip, stop_gateway
    except Exception:
        def check_gateway_egress(*a, **kw): return None
        def ensure_clean_egress(*a, **kw): return None, None
        def get_my_ip(*a, **kw): return None
        def stop_gateway(*a, **kw): pass


HOME = Path.home()
AUTO_FREECF = HOME / "Auto-FreeCF"
SCRIPTS = AUTO_FREECF / "scripts"
PROFILE_DIR = HOME / ".config" / "auto-freecf" / "school-profile"
ENV_FILE = HOME / ".config" / "auto-freecf" / ".env"
ACCOUNTS_FILE = AUTO_FREECF / "github_accounts.json"
SHOTS_DIR = AUTO_FREECF / "debug_github"

GITHUB_SIGNUP = "https://github.com/signup"
GITHUB_EDU = "https://education.github.com/discount_requests/application"

SCHOOL_NAME = "BINUS University"
SCHOOL_NAME_ALT = "Universitas Bina Nusantara"
SCHOOL_DOMAIN = "binus.ac.id"
BASE_LOCAL = "raymondi"          # plus-addressing local part

SCHOOL_MAIL_URL = "https://outlook.office.com/mail/"


# ─────────────────────────────────────────────────────────── env / deps

def _load_env() -> dict:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


_ENV = _load_env()


def cfg(key: str, default: str = "") -> str:
    return os.environ.get(key) or _ENV.get(key, default)


# ─────────────────────────────────────────────────────────── name/pass gen

_WORDS_A = ["swift", "quiet", "amber", "cosmic", "lunar", "rapid", "silent",
            "bright", "nova", "ember", "frost", "solar", "ivory", "cobalt",
            "crimson", "azure", "vivid", "north", "urban", "pixel"]
_WORDS_B = ["fox", "wren", "hawk", "lynx", "otter", "raven", "panda", "koi",
            "heron", "moose", "crane", "finch", "badger", "moth", "gecko",
            "ferret", "tapir", "ibis", "swan", "crab"]


def gen_username() -> str:
    """Human-ish unique-looking username: word + word + digits."""
    a = random.choice(_WORDS_A)
    b = random.choice(_WORDS_B)
    n = random.randint(100, 9999)
    style = random.random()
    if style < 0.34:
        return f"{a}-{b}{n}"
    if style < 0.67:
        return f"{a}{b}{n}"
    return f"{a}_{b}{n}"


def gen_password() -> str:
    """Random strong password (GitHub requires >=15 chars / 8 with number+lower)."""
    tail = "".join(random.choices(string.ascii_letters + string.digits, k=14))
    special = random.choice("!@#$%^&*-_")
    return f"{tail[:7]}{special}{tail[7:]}9Aa"


# ─────────────────────────────────────────────────────────── proxy helpers

def _proxy_dict(proxy: str | None) -> dict | None:
    """Build a Camoufox/Playwright-style proxy dict from a URL string."""
    if not proxy:
        return None
    u = urlparse(proxy if "://" in proxy else f"http://{proxy}")
    server = f"{u.scheme}://{u.hostname}:{u.port}" if u.port else f"{u.scheme}://{u.hostname}"
    d: dict = {"server": server}
    if u.username:
        d["username"] = u.username
    if u.password:
        d["password"] = u.password
    return d


def _load_pool(path: str | None) -> list[str]:
    if not path:
        return []
    p = Path(path).expanduser()
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def pick_proxy(proxy: str | None, pool_file: str | None, index: int) -> tuple[str | None, str]:
    """Resolve the egress: explicit --proxy wins, else rotate the pool by index."""
    if proxy:
        return proxy, "explicit --proxy"
    pool = _load_pool(pool_file)
    if pool:
        chosen = pool[(max(index, 1) - 1) % len(pool)]
        return chosen, f"pool #{((max(index, 1) - 1) % len(pool)) + 1}/{len(pool)}"
    return None, "direct (no proxy)"


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


async def click_button(page, labels: tuple[str, ...], timeout: float = 6.0) -> bool:
    """Click the button whose EXACT label is in `labels` (prefers submit buttons)."""
    deadline = time.time() + timeout
    while True:
        for sel in ("button[type=submit]", "button"):
            loc = page.locator(sel)
            try:
                n = await loc.count()
            except Exception:
                n = 0
            for i in range(n):
                el = loc.nth(i)
                try:
                    txt = (await el.inner_text(timeout=1500)).strip()
                    if txt in labels and await el.is_visible() and await el.is_enabled():
                        await el.click(timeout=5000)
                        return True
                except Exception:
                    continue
        if time.time() >= deadline:
            return False
        await asyncio.sleep(0.4)


async def screenshot(page, name: str) -> str | None:
    try:
        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        path = SHOTS_DIR / f"{time.strftime('%H%M%S')}_{name}.png"
        await page.screenshot(path=str(path))
        print(f"      [shot] {path}", flush=True)
        return str(path)
    except Exception as e:
        print(f"      [shot] failed: {e}", flush=True)
        return None


def _page_text(page):
    return js(page, "document.body ? document.body.innerText : ''", "")


async def detect_captcha(page) -> str | None:
    """Return a description if a human-verification puzzle is present."""
    html = await js(page, "document.documentElement ? document.documentElement.outerHTML : ''", "")
    low = (html or "").lower()
    for needle, label in (
        ("arkoselabs", "Arkose / FunCaptcha"),
        ("funcaptcha", "Arkose / FunCaptcha"),
        ("hcaptcha", "hCaptcha"),
        ("recaptcha", "reCAPTCHA"),
        ("challenge-container", "GitHub challenge iframe"),
        ("verify you are human", "human-verification prompt"),
        ("enable javascript", "GitHub anti-bot page (enable JavaScript)"),
        ("disable your ad blocker", "GitHub anti-bot page (disable adblocker)"),
        ("disable adblocker", "GitHub anti-bot page (disable adblocker)"),
        ("unusual activity", "GitHub anti-bot page (unusual activity)"),
    ):
        if needle in low:
            return label
    txt = (await _page_text(page) or "").lower()
    if "verify" in txt and "human" in txt:
        return "human-verification prompt (text)"
    return None


async def detect_access_restriction(page) -> str | None:
    """
    GitHub sometimes serves a network-level block instead of the signup form:
    'Access is temporarily restricted' / 'We detected unusual activity'.

    This is NOT solvable by the script — it is tied to the egress IP (often a
    shared WARP/Cloudflare or datacenter range). Detect it so we report honestly
    instead of a misleading 'input not found'.

    Two signatures are checked:
      1. Visible text (when the interstitial renders its DOM).
      2. Structural: an anti-bot challenge page with NO inputs and NO visible
         body text but a `dd={...}` / `cmsg` challenge payload (DataDome-style),
         which is what a blocked network returns.
    """
    body = (await _page_text(page) or "")
    low = body.lower()
    if "temporarily restricted" in low or "unusual activity from your device" in low:
        m = re.search(r'\((IP [^)]+)\)', body)
        ip = m.group(1) if m else ""
        return f"network/IP block: 'Access is temporarily restricted' {ip}".strip()
    if "automated (bot) activity on your network" in low:
        return "network/IP block: bot activity flagged on this network"

    n_input = await js(page, "document.querySelectorAll('input').length", -1)
    if n_input == 0:
        html = await js(page, "document.documentElement ? document.documentElement.innerHTML : ''", "")
        challengeish = any(k in (html or "") for k in ("dd={", "cmsg", "challenge", "__dd",
                                                       "datadome", "cf-chl", "challenge-platform"))
        if challengeish:
            return ("network/IP block: anti-bot challenge page (no form rendered). "
                    "GitHub is challenging this egress IP — switch proxy/VPN and retry.")
        if not low.strip():
            return ("no signup form rendered and page body empty — likely an anti-bot "
                    "challenge/network block. Switch egress IP and retry.")
    return None


# ─────────────────────────────────────────────────────────── school mailbox

async def school_login(page, email: str, password: str) -> None:
    """Walk the Microsoft login flow (email -> password -> stay signed in)."""
    print("      [school] completing Microsoft login…", flush=True)
    await type_into(page, "input[type=email], input[name=loginfmt]", email, "school-email")
    await asyncio.sleep(0.5)
    await click_button(page, ("Next", "Sign in"), timeout=6)
    await asyncio.sleep(4)

    for _ in range(25):
        if await visible_first(page, "input[type=password], input[name=passwd]") is not None:
            break
        await asyncio.sleep(1)
    await type_into(page, "input[type=password], input[name=passwd]", password, "school-password")
    await asyncio.sleep(0.5)
    await click_button(page, ("Sign in", "Next"), timeout=6)
    await asyncio.sleep(6)

    for label in ("Yes", "No"):
        try:
            btn = page.get_by_role("button", name=re.compile(rf"^\s*{label}\s*$"))
            if await btn.count() and await btn.first.is_visible():
                await btn.first.click(timeout=4000)
                print(f"      [school] answered '{label}' to stay-signed-in", flush=True)
                break
        except Exception:
            continue
    await asyncio.sleep(6)


async def wait_inbox(page, timeout: float = 60) -> bool:
    for _ in range(int(timeout)):
        u = await live_url(page)
        if "outlook" in u and ("mail" in u or "owa" in u):
            if await has(page, "[role=main], [aria-label*=Message], div[role=list]"):
                return True
        await asyncio.sleep(1)
    return False


async def read_inbox_text(page) -> list[str]:
    """Best-effort scrape of visible list items + page text for OTP scanning."""
    raw = await js(page, """JSON.stringify(
        [...document.querySelectorAll('[role=option],[role=listitem],[aria-label]')]
        .map(e=>(e.innerText||'').trim())
        .filter(t=>t && t.length>2).slice(0,25))""", "[]")
    items: list[str] = []
    try:
        items = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except Exception:
        items = []
    body = await _page_text(page)
    if body:
        items.append(body)
    return items


def _extract_launch_code(texts: list[str]) -> str | None:
    """GitHub emails an 8-digit launch code. Find it in subjects/body text."""
    joined = "\n".join(t for t in texts if t)
    for pat in (
        r'launch code[:\s]*\b(\d{8})\b',
        r'\b(\d{8})\b\s*(?:is your|as your) launch code',
        r'your GitHub launch code[^0-9]{0,40}(\d{8})',
        r'verification code[:\s]*\b(\d{8})\b',
    ):
        m = re.search(pat, joined, re.I)
        if m:
            return m.group(1)
    if re.search(r"github", joined, re.I):
        m = re.search(r'\b(\d{8})\b', joined)
        if m:
            return m.group(1)
    return None


def _read_launch_code_via_helper(email: str) -> str | None:
    """
    Defensive reuse of the shared school-mail reader module if it exposes an
    async launch-code helper (other worker is converting it to Camoufox).

    Any of these entry points are tried, in order:
      school_mail_browser.read_launch_code
      school_mail_browser.wait_inbox / read_subjects
      sheerid_link_finder (link finder — not a code, skipped)
    Returns the code, or None to fall back to the built-in Camoufox reader.
    """
    for modname in ("school_mail_browser",):
        mod = None
        for imp in (modname, f"scripts.{modname}"):
            try:
                mod = __import__(imp, fromlist=["*"])
                break
            except Exception:
                continue
        if mod is None:
            continue
        fn = getattr(mod, "read_launch_code", None)
        if callable(fn):
            print(f"      [school] using {modname}.read_launch_code()", flush=True)
            try:
                res = fn(email)
                return asyncio.run(res) if asyncio.iscoroutine(res) else res
            except Exception as e:  # noqa: BLE001
                print(f"      [school] helper failed ({e}); falling back", flush=True)
    return None


async def read_launch_code(email: str, timeout: int = 240, proxy: str | None = None) -> str | None:
    """
    Read GitHub's 8-digit launch code from the BINUS mailbox.

    Tries the shared school-mail reader module first (defensive import), then
    falls back to opening Outlook Web in Camoufox with the persistent school
    profile (~/.config/auto-freecf/school-profile).
    """
    # 1. shared helper (defensive)
    code = _read_launch_code_via_helper(email)
    if code:
        print(f"      [school] ✓ launch code (helper): {code}", flush=True)
        return code

    if AsyncCamoufox is None:
        print(f"      [school] ✗ Camoufox unavailable: {_CAMOUFOX_IMPORT_ERROR}", flush=True)
        return None

    pw = cfg("SCHOOL_MAIL_PASSWORD")
    url = cfg("SCHOOL_MAIL_URL", SCHOOL_MAIL_URL)
    if not pw:
        print("      [school] ✗ SCHOOL_MAIL_PASSWORD not set in .env", flush=True)
        return None

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"      [school] launching Camoufox mailbox reader (profile={PROFILE_DIR})", flush=True)
    kwargs: dict = dict(headless=False, geoip=True, humanize=True, os="windows",
                        persistent_context=True, user_data_dir=str(PROFILE_DIR))
    pd = _proxy_dict(proxy)
    if pd:
        kwargs["proxy"] = pd

    # persistent_context=True yields a BrowserContext directly.
    async with AsyncCamoufox(**kwargs) as ctx:
        page = await ctx.new_page()
        try:
            if is_gateway(proxy):
                await apply_gateway_session(ctx, "school-mail")
            await page.goto(url, wait_until="domcontentloaded")
            await asyncio.sleep(8)
            title = await js(page, "document.title", "")
            cur = await live_url(page)
            print(f"      [school] page: {title!r} @ {cur[:70]}", flush=True)
            if any(k in cur for k in ("login.microsoftonline", "login.live", "adfs")) or "sign in" in (title or "").lower():
                await school_login(page, email, pw)
            ok = await wait_inbox(page, timeout=60)
            print(f"      [school] inbox ready: {ok}", flush=True)

            start = time.time()
            while time.time() - start < timeout:
                texts = await read_inbox_text(page)
                code = _extract_launch_code(texts)
                if code:
                    print(f"      [school] ✓ launch code: {code}", flush=True)
                    return code
                await asyncio.sleep(6)
            print("      [school] no launch code within timeout", flush=True)
            return None
        finally:
            pass  # context manager closes the browser


# ─────────────────────────────────────────────────────────── github signup

async def do_signup(page, email: str, password: str, username: str,
                    dry_run: bool, max_user_tries: int = 6) -> dict:
    """
    Walk https://github.com/signup as far as possible.

    Returns a dict with `success`, `username`, `stage`, `blocked`, `notes`.
    Never touches an existing account. On --dry-run it fills and screenshots but
    does not click the final account-creating buttons.
    """
    res: dict = {"success": False, "stage": "start", "blocked": None, "notes": []}

    await page.goto(GITHUB_SIGNUP, wait_until="domcontentloaded")
    await asyncio.sleep(6)
    await screenshot(page, "01_signup")

    # Network-level block? Report honestly instead of a bogus selector error.
    blocked = await detect_access_restriction(page)
    if blocked:
        res["stage"] = "access_restricted"
        res["blocked"] = blocked
        res["notes"].append("Switch egress IP (different proxy/VPN) and retry; "
                            "GitHub is blocking this network, not our selectors.")
        await screenshot(page, "00_access_restricted")
        print(f"      [gh] ❌ {blocked}", flush=True)
        return res

    # ── Step 1: email ──────────────────────────────────────────────
    print("      [gh] step 1/4: email", flush=True)
    if not await fill_first_input(page, ['input#email', 'input[name=email]',
                                         'input[type=email]', 'input[autocomplete=email]'], email):
        blocked = await detect_access_restriction(page)
        res["stage"] = "email_input_not_found"
        if blocked:
            res["blocked"] = blocked
        res["notes"].append(f"url={await live_url(page)}")
        await screenshot(page, "01_email_missing")
        return res
    res["stage"] = "email_filled"
    await asyncio.sleep(0.5)

    cap = await detect_captcha(page)
    if cap:
        res["blocked"] = f"captcha:{cap}"
        res["stage"] = "captcha_after_email"
        await screenshot(page, "captcha_email")
        print(f"      [gh] ❌ blocked by {cap} — human required", flush=True)
        return res

    if dry_run:
        await screenshot(page, "01_email_dryrun")
        res["stage"] = "dry_run_stopped_after_email"
        res["notes"].append("dry-run: stopped before clicking 'Create account'")
        return res

    await click_button(page, ("Continue", "Create account", "Sign up"))
    await asyncio.sleep(5)
    await screenshot(page, "02_after_email")

    cap = await detect_captcha(page)
    if cap:
        res["blocked"] = f"captcha:{cap}"
        res["stage"] = "captcha_after_email_submit"
        await screenshot(page, "captcha_after_email_submit")
        print(f"      [gh] ❌ blocked by {cap} — human required", flush=True)
        return res

    # ── Step 2: password ───────────────────────────────────────────
    print("      [gh] step 2/4: password", flush=True)
    if await has(page, "input[type=password]"):
        if not await fill_first_input(page, ['input#password', 'input[name=password]',
                                             'input[type=password]'], password):
            res["stage"] = "password_input_not_found"
            return res
        res["stage"] = "password_filled"
        await asyncio.sleep(0.5)
    else:
        res["notes"].append("no password field seen (maybe email-only step)")

    if dry_run:
        await screenshot(page, "02_password_dryrun")
        res["stage"] = "dry_run_stopped_after_password"
        return res

    await click_button(page, ("Continue", "Next"))
    await asyncio.sleep(5)
    await screenshot(page, "03_after_password")

    cap = await detect_captcha(page)
    if cap:
        res["blocked"] = f"captcha:{cap}"
        res["stage"] = "captcha_after_password"
        await screenshot(page, "captcha_after_password")
        print(f"      [gh] ❌ blocked by {cap} — human required", flush=True)
        return res

    # ── Step 3: username (availability retry) ──────────────────────
    print("      [gh] step 3/4: username", flush=True)
    if await has(page, "input#login, input[name=login]"):
        for attempt in range(max_user_tries):
            uname = username if attempt == 0 else gen_username()
            if not await fill_first_input(page, ['input#login', 'input[name=login]'], uname):
                res["stage"] = "username_input_not_found"
                return res
            await asyncio.sleep(2)  # let the availability check fire
            err = await js(page, """(()=>{const e=document.querySelector('[aria-live],.error,.flash-error,p.color-fg-danger');
                return e ? (e.innerText||'').trim() : '';})()""", "")
            taken = bool(err and re.search(r'taken|not available|already', err, re.I))
            if not taken:
                username = uname
                res["username"] = username
                res["stage"] = "username_ok"
                print(f"      [gh] username ok: {username}", flush=True)
                break
            print(f"      [gh] username taken: {uname} (retry)", flush=True)
        else:
            res["stage"] = "username_unavailable"
            return res
        await asyncio.sleep(0.5)
    else:
        res["notes"].append("no username field seen")
        res["username"] = username

    if dry_run:
        await screenshot(page, "03_username_dryrun")
        res["stage"] = "dry_run_stopped_after_username"
        res["notes"].append("dry-run: stopped before creating the account")
        return res

    await click_button(page, ("Continue", "Create account"))
    await asyncio.sleep(5)
    await screenshot(page, "04_after_username")

    cap = await detect_captcha(page)
    if cap:
        res["blocked"] = f"captcha:{cap}"
        res["stage"] = "captcha_before_create"
        await screenshot(page, "captcha_before_create")
        print(f"      [gh] ❌ blocked by {cap} — human required", flush=True)
        return res

    # ── Step 4: email verification (8-digit launch code) ───────────
    print("      [gh] step 4/4: email launch code", flush=True)
    body = (await _page_text(page) or "").lower()
    if "launch code" in body or "verify" in body or "email" in body:
        code_field = await js(page, """(()=>{const cands=['input[name=code]','input#code',
            'input[autocomplete=one-time-code]','input[inputmode=numeric]','input[maxlength="8"]'];
            for(const s of cands){ if(document.querySelector(s)) return s; } return null;})()""", None)

        print("      [gh] waiting for email launch code from mailbox…", flush=True)
        code = await read_launch_code(email, timeout=240)
        if not code:
            res["stage"] = "launch_code_timeout"
            res["blocked"] = "email_launch_code_not_received"
            await screenshot(page, "05_code_timeout")
            return res

        filled = False
        if code_field:
            filled = await type_into(page, code_field, code, "launch-code")
        if not filled:
            filled = await fill_first_input(page, ['input[name=code]', 'input#code',
                                                   'input[autocomplete=one-time-code]',
                                                   'input[inputmode=numeric]'], code)
        if not filled:
            res["blocked"] = "launch_code_field_not_found"
            res["stage"] = "code_enter_failed"
            res["notes"].append(f"code={code} (enter manually)")
            await screenshot(page, "05_code_field_missing")
            return res
        res["notes"].append("launch_code_entered")
        await asyncio.sleep(1)
        await click_button(page, ("Continue", "Verify"))
        await asyncio.sleep(6)
        await screenshot(page, "06_after_code")

    # ── done? ──────────────────────────────────────────────────────
    url = await live_url(page)
    text = (await _page_text(page) or "").lower()
    if "github.com" in url and ("welcome" in text or "dashboard" in text or url.rstrip("/").endswith("github.com")):
        res["success"] = True
        res["stage"] = "account_created"
    elif "welcome" in text or "you're all set" in text or "your account" in text:
        res["success"] = True
        res["stage"] = "account_created"
    else:
        res["stage"] = "post_code_unknown"
        res["notes"].append(f"url={url}")
    return res


# ─────────────────────────────────────────────────────────── github education

async def do_education(page, email: str, username: str, dry_run: bool) -> dict:
    """
    Open the Education application and fill what is safe. STOP before any
    attestation/photo step. Never submit an attestation.
    """
    res: dict = {"stage": "start", "filled": [], "needs_human": [], "url": None}

    await page.goto(GITHUB_EDU, wait_until="domcontentloaded")
    await asyncio.sleep(7)
    await screenshot(page, "10_education")
    res["url"] = await live_url(page)

    blocked = await detect_access_restriction(page)
    if blocked:
        res["stage"] = "access_restricted"
        res["needs_human"].append(f"{blocked} — switch egress IP and retry.")
        return res

    body = (await _page_text(page) or "")
    low = body.lower()
    if "sign in" in low and "application" not in low:
        res["stage"] = "needs_login"
        res["needs_human"].append("Sign in to the new GitHub account, then re-open the Education form.")
        return res

    print("      [edu] filling application fields (best-effort)", flush=True)

    if await fill_first_input(page, ['input[type=email]', 'input[name*=email]',
                                     'input[id*=email]'], email):
        res["filled"].append("email")

    if await fill_first_input(page, ['input[name*=school]', 'input[id*=school]',
                                     'input[placeholder*=school]', 'input[placeholder*=School]'], SCHOOL_NAME):
        res["filled"].append(f"school={SCHOOL_NAME}")
        await asyncio.sleep(2)
        try:
            opt = page.locator("li, [role=option]").filter(has_text=re.compile("binus", re.I))
            if await opt.count():
                await opt.first.click(timeout=4000)
                res["filled"].append("school_autocomplete=binus")
        except Exception:
            pass

    if dry_run:
        res["stage"] = "dry_run_stopped"
        res["needs_human"].append("Review filled fields, then complete manually.")
        await screenshot(page, "11_education_dryrun")
        return res

    low = (await _page_text(page) or "").lower()
    if any(k in low for k in ("i attest", "attest", "upload", "student id",
                              "proof of enrollment", "enrollment", "school-issued",
                              "photo of", "documentation")):
        res["stage"] = "stopped_before_attestation"
        res["needs_human"].append(
            "Attestation / photo of school ID is required here. This is a human step "
            "(legal attestation + document upload + GitHub's manual review).")
        await screenshot(page, "12_attestation_stop")
        return res

    res["stage"] = "form_filled_review"
    res["needs_human"].append("Verify fields and click Submit yourself; review is manual.")
    await screenshot(page, "13_education_ready")
    return res


# ─────────────────────────────────────────────────────────── persistence

def save_account(record: dict) -> None:
    data = []
    if ACCOUNTS_FILE.exists():
        try:
            data = json.loads(ACCOUNTS_FILE.read_text())
            if isinstance(data, dict):
                data = data.get("accounts", [])
        except Exception:
            data = []
    data = [d for d in data if d.get("email") != record.get("email")]
    data.append(record)
    ACCOUNTS_FILE.write_text(json.dumps(
        {"accounts": data, "updated": datetime.now(timezone.utc).isoformat()},
        indent=2) + "\n")
    try:
        os.chmod(ACCOUNTS_FILE, 0o600)
    except Exception:
        pass
    print(f"      [+] saved -> {ACCOUNTS_FILE}", flush=True)


# ─────────────────────────────────────────────────────────── --check

def run_check() -> int:
    print("=" * 60)
    print("  github_farm --check (Camoufox)")
    print("=" * 60)
    ok = True

    if AsyncCamoufox is None:
        ok = False
        print(f"  ❌ camoufox          {_CAMOUFOX_IMPORT_ERROR}")
    else:
        try:
            import camoufox  # noqa
            print(f"  ✅ camoufox          ({camoufox.__file__})")
        except Exception:
            print("  ✅ camoufox          (imported)")

    try:
        import playwright  # noqa
        print(f"  ✅ playwright        ({playwright.__file__})")
    except Exception as e:
        ok = False
        print(f"  ❌ playwright        {e}")

    print(f"  {'✅' if CamoufoxProxy is not None else '➖'} camoufox.ip.Proxy   "
          f"({'available' if CamoufoxProxy is not None else 'fallback to dict'})")

    for modname in ("school_mail_browser", "sheerid_link_finder", "gateway_session"):
        found = (SCRIPTS / f"{modname}.py").exists()
        print(f"  {'✅' if found else '➖'} {modname:20s} ({SCRIPTS / (modname + '.py')})")

    print(f"  {'✅' if PROFILE_DIR.exists() else '➖'} school profile dir  ({PROFILE_DIR})"
          f"{'' if PROFILE_DIR.exists() else ' (created on first run / needs a manual MFA login once)'}")

    smail = cfg("SCHOOL_EMAIL")
    spw = bool(cfg("SCHOOL_MAIL_PASSWORD"))
    print(f"  {'✅' if smail else '❌'} SCHOOL_EMAIL         {smail or '(unset in .env)'}")
    print(f"  {'✅' if spw else '❌'} SCHOOL_MAIL_PASSWORD {'set' if spw else '(unset in .env)'}")
    if not smail or not spw:
        ok = False

    print(f"  {'✅' if ACCOUNTS_FILE.parent.exists() else '❌'} output dir           ({ACCOUNTS_FILE.parent})")
    print(f"  ℹ️  accounts file       {ACCOUNTS_FILE}")
    print(f"  ℹ️  mailbox URL         {cfg('SCHOOL_MAIL_URL', SCHOOL_MAIL_URL)}")
    print(f"  ℹ️  target signup       {GITHUB_SIGNUP}")
    print(f"  ℹ️  target education    {GITHUB_EDU}")
    print("  ℹ️  run with: /home/amen/.local/share/auto-freecf/camoufox-venv/bin/python")
    print("\n  NOTE: Arkose/CAPTCHA and the Education photo+attestation step are NOT automatable.")
    print("=" * 60)
    return 0 if ok else 1


# ─────────────────────────────────────────────────────────── main flow

def build_email(index: int) -> str:
    """Plus-addressed school mailbox: raymondi+gh<N>@binus.ac.id."""
    if index <= 0:
        return f"{BASE_LOCAL}@{SCHOOL_DOMAIN}"    # plain mailbox (careful!)
    return f"{BASE_LOCAL}+gh{index}@{SCHOOL_DOMAIN}"


async def run(index: int, headless: bool, proxy: str | None, pool: str | None,
              dry_run: bool, no_proxy: bool = False) -> int:
    if AsyncCamoufox is None:
        print("=" * 60, flush=True)
        print("  ✗ Camoufox is not importable in this interpreter.", flush=True)
        print(f"    {_CAMOUFOX_IMPORT_ERROR}", flush=True)
        print("  Run under the camoufox venv:", flush=True)
        print("    /home/amen/.local/share/auto-freecf/camoufox-venv/bin/python "
              "scripts/github_farm.py …", flush=True)
        print("=" * 60, flush=True)
        return 2

    email = build_email(index)
    username = gen_username()
    password = gen_password()

    gateway_proc = None
    if proxy:
        chosen_proxy, proxy_src = proxy, "explicit --proxy"
    elif pool:
        chosen_proxy, proxy_src = pick_proxy(None, pool, index)
    elif no_proxy:
        chosen_proxy, proxy_src = None, "direct (--no-proxy)"
    else:
        print("  [egress] No proxy specified; acquiring clean proxy gateway…", flush=True)
        prefer_pool = AUTO_FREECF / "signup_from_scratch" / "proxies.txt"
        gw_url, proc = ensure_clean_egress(prefer_pool=prefer_pool)
        if gw_url and proc:
            chosen_proxy = gw_url
            proxy_src = "auto clean egress gateway"
            gateway_proc = proc
        else:
            print("  [egress] ⚠ Could not acquire clean proxy gateway; continuing direct", flush=True)
            chosen_proxy = None
            proxy_src = "direct (clean egress failed)"

    # Resolve active egress IP for visibility
    if chosen_proxy:
        egress_ip = check_gateway_egress(chosen_proxy, retries=2, timeout=5.0) or "(gateway exit)"
    else:
        egress_ip = get_my_ip(timeout=4.0) or "(direct host)"

    is_gw = bool(is_gateway(chosen_proxy) or gateway_proc is not None)

    print("=" * 60, flush=True)
    print("  GITHUB FARM (Camoufox / Playwright)", flush=True)
    print("=" * 60, flush=True)
    print(f"  index     : {index}", flush=True)
    print(f"  email     : {email}", flush=True)
    print(f"  username  : {username}", flush=True)
    print(f"  password  : {password}", flush=True)
    print(f"  dry-run   : {dry_run}", flush=True)
    print(f"  headless  : {headless}", flush=True)
    print(f"  egress    : {chosen_proxy or '(direct)'}  [{proxy_src}]", flush=True)
    print(f"  egress IP : {egress_ip}", flush=True)
    print(f"  gateway   : {is_gw}", flush=True)
    print("-" * 60, flush=True)

    if not headless and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        print("      [!] no DISPLAY found; headed Firefox will fail. "
              "Use --headless (Xvfb 'virtual') or run under a desktop / xvfb-run.", flush=True)

    record = {
        "email": email,
        "username": username,
        "password": password,
        "index": index,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "proxy": chosen_proxy or None,
        "push_to_note": proxy_src,
        "note": "School mailbox is the ONLY address used (plus-addressing). Does not touch 'Ini-Amin'.",
    }

    kwargs: dict = dict(
        headless="virtual" if headless else False,  # 'virtual' = Xvfb, avoids headless leaks
        geoip=True,
        humanize=True,
        os="windows",
    )
    pd = _proxy_dict(chosen_proxy)
    if pd:
        kwargs["proxy"] = pd
    # Camoufox text-entry logging can leak the launch code; quiet it a touch.
    try:
        async with AsyncCamoufox(**kwargs) as browser:
            page = await browser.new_page()
            context = page.context
            if is_gw:
                await apply_gateway_session(context, email)
                await apply_gateway_session(page, email)
                print(f"      📌 Sticky gateway session applied (id={email})", flush=True)

            try:
                await page.goto(GITHUB_SIGNUP, wait_until="domcontentloaded")
                await asyncio.sleep(6)

                signup = await do_signup(page, email, password, username, dry_run)
                record["signup"] = signup
                if signup.get("username"):
                    record["username"] = signup["username"]

                print("\n  --- signup result ---", flush=True)
                print(f"      success : {signup.get('success')}", flush=True)
                print(f"      stage   : {signup.get('stage')}", flush=True)
                print(f"      blocked : {signup.get('blocked')}", flush=True)
                for n in signup.get("notes", []):
                    print(f"      note    : {n}", flush=True)

                edu = {"stage": "skipped", "needs_human": ["Signup did not complete."]}
                if signup.get("success") or (dry_run and signup.get("stage") != "access_restricted"):
                    try:
                        edu = await do_education(page, email, record["username"], dry_run)
                    except Exception as e:
                        edu = {"stage": "error", "needs_human": [f"Education navigation failed: {e}"]}
                record["education"] = edu

                print("\n  --- education result ---", flush=True)
                print(f"      stage   : {edu.get('stage')}", flush=True)
                print(f"      filled  : {', '.join(edu.get('filled', [])) or '(none)'}", flush=True)
                for h in edu.get("needs_human", []):
                    print(f"      human → : {h}", flush=True)

                # Only persist a usable account. A blocked/failed signup must NOT
                # be written as if it succeeded (it would look like a real account).
                if signup.get("success") and not dry_run:
                    save_account(record)
                else:
                    print(f"      [skip] not saved to {ACCOUNTS_FILE.name} "
                          f"(signup success={signup.get('success')}, "
                          f"stage={signup.get('stage')}, dry_run={dry_run})", flush=True)
                return 0 if signup.get("success") or dry_run else 1
            finally:
                if not headless:
                    print("\n  browser left open 8s for inspection…", flush=True)
                    await asyncio.sleep(8)
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ browser/flow error: {type(e).__name__}: {e}", flush=True)
        record["error"] = f"{type(e).__name__}: {e}"
        # do not persist a record for a crashed flow
        return 1
    finally:
        if gateway_proc is not None:
            stop_gateway(gateway_proc)
            print("  [egress] Stopped clean egress gateway.", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="GitHub signup + Education application helper (Camoufox). "
                    "Automates form fill + email launch-code; Arkose/captcha and "
                    "identity attestation remain human steps.")
    ap.add_argument("--check", action="store_true", help="check deps + mailbox config, then exit")
    ap.add_argument("--index", type=int, default=1, help="N for raymondi+gh<N>@binus.ac.id")
    ap.add_argument("--headless", action="store_true", help="run under Xvfb ('virtual') headless")
    ap.add_argument("--proxy", default=None,
                    help="proxy URL; supports the KancaHub gateway http://127.0.0.1:8888 (X-Session-ID)")
    ap.add_argument("--pool", default=None,
                    help="proxy list file; one chosen per --index for rotation")
    ap.add_argument("--no-proxy", action="store_true",
                    help="force direct connection (bypass auto clean egress gateway)")
    ap.add_argument("--dry-run", action="store_true",
                    help="walk the signup flow, screenshot, but do not submit/create")
    args = ap.parse_args()

    if args.check:
        return run_check()
    return asyncio.run(run(args.index, args.headless, args.proxy, args.pool, args.dry_run, no_proxy=args.no_proxy))


if __name__ == "__main__":
    sys.exit(main())
