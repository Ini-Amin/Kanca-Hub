#!/usr/bin/env python3
"""
ChatGPT K-12 flow on Camoufox (anti-detect Firefox, Playwright API).

Port of scripts/k12_nodriver.py. Same flow:

    relay mailbox -> chatgpt signup -> OTP -> about-you age gate
    -> k12-verification -> SheerID anchor / popup capture -> K12Verifier

Differences from the nodriver version:
  * Firefox/Gecko via Playwright (Juggler), NOT CDP. Mouse/keyboard input done by
    Playwright is trusted (isTrusted=true), so the CDP click/insertText helpers
    and the window.open monkey-patch are replaced by locator.click / fill /
    press_sequentially / page.expect_popup().
  * AsyncCamoufox(geoip=True, humanize=True, os="windows"): timezone/locale/geo
    and WebRTC IP follow the exit IP (or --proxy); the cursor moves along human
    Bezier curves.

Must run under the isolated venv (python3.11 + playwright==1.62.0):

    /home/amen/.local/share/auto-freecf/camoufox-venv/bin/python \
        /home/amen/Auto-FreeCF/scripts/k12_camoufox.py

Mail: KancaHub's own relay (Supabase + Cloudflare Email Routing), same config
as k12_nodriver.py. Sessions are captured from /api/auth/session and saved for
9Router.
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
from pathlib import Path
from urllib.parse import urlparse

import requests
from camoufox.async_api import AsyncCamoufox
from playwright.async_api import Error as PWError
from playwright.async_api import TimeoutError as PWTimeout

HOME = Path.home()
AUTO_FREECF = HOME / "Auto-FreeCF"
K12_DIR = HOME / "petani-proxy" / "Farm-Acc-ChatGPT-K-12-Teachers" / "PyRuntime_64"
SESSIONS = K12_DIR / "k12_sessions.json"
CREATED = K12_DIR / "created_k12_accounts.txt"

CHATGPT = "https://chatgpt.com"

BUTTON_LABELS = ("Continue", "Next", "Sign up", "Verify", "Submit", "Log in")
SESSION_COOKIES = ("__Secure-next-auth.session-token", "next-auth.session-token",
                   "__Secure-authjs.session-token")


# ───────────────────────────────────────────── config / mail relay
# (identical to k12_nodriver.py)

def _load_env() -> dict:
    env = {}
    p = HOME / ".config" / "auto-freecf" / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


_ENV = _load_env()
_CFG = {}
try:
    _CFG = json.loads((AUTO_FREECF / "signup_from_scratch" / "config.json").read_text())
except Exception:
    pass

SUPABASE_URL = (_ENV.get("SUPABASE_URL") or "").rstrip("/")
MAIL_KEY = _ENV.get("TMK_KEY") or _CFG.get("mail_api_key") or ""
if not SUPABASE_URL and _CFG.get("mail_api"):
    m = re.match(r"(https://[^/]+)", _CFG["mail_api"])
    SUPABASE_URL = m.group(1) if m else ""
MAIL_BASE = f"{SUPABASE_URL}/functions/v1/temp-mail-api"
DOMAINS = (_ENV.get("K12_DOMAINS") or ",".join(_CFG.get("mail_domains") or ["kancalabs.biz.id"])).split(",")


# Pluggable mail provider: K12_MAIL_PROVIDER = 'relay' (default) | 'mailtm'
MAIL_PROVIDER = (os.environ.get("K12_MAIL_PROVIDER") or _ENV.get("K12_MAIL_PROVIDER") or "relay").strip().lower()
MAILTM_BASE = "https://api.mail.tm"
MAILTM_DOMAIN = "maxxspace.com"  # the only mail.tm domain
_MAILTM_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _relay_create_mailbox() -> dict:
    dom = random.choice([d.strip() for d in DOMAINS if d.strip()])
    r = requests.post(f"{MAIL_BASE}/new_address", json={"domain": dom},
                      headers={"x-api-key": MAIL_KEY}, timeout=60)
    r.raise_for_status()
    return r.json()  # {address, jwt, domain}


def _relay_poll_mail(jwt: str) -> list[dict]:
    r = requests.get(f"{MAIL_BASE}/parsed_mails",
                     headers={"Authorization": f"Bearer {jwt}", "x-api-key": MAIL_KEY}, timeout=30)
    r.raise_for_status()
    d = r.json()
    return d if isinstance(d, list) else d.get("results", [])


def _mailtm_create_mailbox() -> dict:
    """Register a random address on mail.tm and fetch its JWT."""
    local = "k" + "".join(random.choices(string.ascii_lowercase + string.digits, k=11))
    address = f"{local}@{MAILTM_DOMAIN}"
    pw = "".join(random.choices(string.ascii_letters + string.digits, k=16))
    r = requests.post(f"{MAILTM_BASE}/accounts", json={"address": address, "password": pw},
                      headers=_MAILTM_HEADERS, timeout=30)
    r.raise_for_status()
    r = requests.post(f"{MAILTM_BASE}/token", json={"address": address, "password": pw},
                      headers=_MAILTM_HEADERS, timeout=30)
    r.raise_for_status()
    return {"address": address, "token": r.json()["token"], "provider": "mailtm"}


def _mailtm_poll_mail(token: str) -> list[dict]:
    """List messages, fetch each in full, normalise to the keys wait_for_otp expects."""
    h = {**_MAILTM_HEADERS, "Authorization": f"Bearer {token}"}
    r = requests.get(f"{MAILTM_BASE}/messages", headers=h, timeout=30)
    r.raise_for_status()
    d = r.json()
    items = d if isinstance(d, list) else (d.get("hydra:member") or d.get("member") or [])
    out = []
    for it in items:
        full = it
        try:
            fr = requests.get(f"{MAILTM_BASE}/messages/{it['id']}", headers=h, timeout=30)
            fr.raise_for_status()
            full = fr.json()
        except Exception:  # noqa: BLE001  fall back to the list summary (subject/intro)
            pass
        frm = full.get("from") or it.get("from") or {}
        if isinstance(frm, dict):
            frm = f"{frm.get('name', '')} <{frm.get('address', '')}>".strip()
        html = full.get("html") or ""
        if isinstance(html, list):
            html = "\n".join(str(x) for x in html)
        text = full.get("text") or ""
        out.append({
            "id": it.get("id"),
            "subject": full.get("subject") or it.get("subject") or "",
            "text": text,
            "body": text,
            "html": html,
            "snippet": full.get("intro") or it.get("intro") or "",
            "from": frm,
        })
    return out


def create_mailbox() -> dict:
    if MAIL_PROVIDER == "mailtm":
        return _mailtm_create_mailbox()
    return _relay_create_mailbox()


def poll_mail(jwt: str) -> list[dict]:
    if MAIL_PROVIDER == "mailtm":
        return _mailtm_poll_mail(jwt)
    return _relay_poll_mail(jwt)


def _blob(m: dict) -> str:
    return " ".join(str(m.get(k, "")) for k in ("subject", "text", "body", "html", "snippet", "from"))


async def wait_for_otp(jwt: str, timeout: int = 180) -> str | None:
    print(f"      [mail] polling relay for OTP (max {timeout}s)…", flush=True)
    start = time.time()
    seen = set()
    while time.time() - start < timeout:
        try:
            for m in await asyncio.to_thread(poll_mail, jwt):
                mid = m.get("id") or m.get("message_id")
                if mid in seen:
                    continue
                blob = re.sub(r"<[^>]+>", " ", _blob(m))
                low = blob.lower()
                if any(k in low for k in ("openai", "chatgpt", "verification code", "verify", "code", "login")):
                    for pat in (r'code to continue:?\s*(\d{6})', r'verification code:?\s*(\d{6})', r'\b(\d{6})\b'):
                        mm = re.search(pat, blob, re.I)
                        if mm:
                            print(f"      [+] OTP: {mm.group(1)}", flush=True)
                            return mm.group(1)
                    seen.add(mid)
        except Exception as e:  # noqa: BLE001
            print(f"      [mail] {e}", flush=True)
        await asyncio.sleep(5)
    return None


# ───────────────────────────────────────────── playwright helpers

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


async def click_continue(page, timeout: float = 5.0) -> bool:
    """Click the EXACT 'Continue' button (never 'Continue with Google/Apple/phone').

    Matches the label exactly (case-sensitive, trimmed), preferring submit buttons.
    """
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
                    if txt in BUTTON_LABELS and await el.is_visible() and await el.is_enabled():
                        await el.click(timeout=5000)
                        return True
                except Exception:
                    continue
        if time.time() >= deadline:
            return False
        await asyncio.sleep(0.5)


# ───────────────────────────────────────────── SheerID capture

def _is_sheerid(url: str) -> bool:
    return bool(url) and "sheerid.com/verify" in url


async def find_sheerid_anchor(page) -> str:
    """Return the SheerID URL from the DOM, if present.

    The K-12 page delivers the SheerID URL as a plain <a href="https://services.
    sheerid.com/verify/...">. Reading it is more reliable than clicking.
    """
    # 1) anchor href
    href = await js(page, """(()=>{for(const a of document.querySelectorAll('a[href]')){
        const h=a.href||''; if(h.includes('sheerid.com/verify')) return h;} return '';})()""", "")
    if href:
        return href
    # 2) any element with the URL in an attribute
    href = await js(page, """(()=>{const els=document.querySelectorAll('[href],[data-href],[data-url]');
        for(const e of els){ for(const a of ['href','data-href','data-url']){
        const v=e.getAttribute(a)||''; if(v.includes('sheerid.com/verify')) return v; }} return '';})()""", "")
    if href:
        return href
    # 3) full HTML (covers React-embedded URLs / __NEXT_DATA__)
    html = await js(page, "document.documentElement ? document.documentElement.outerHTML : ''", "")
    if html:
        m = re.search(r'https://services\.sheerid\.com/verify/[^\s"\'<>\\]+', html)
        if m:
            return m.group(0)
    # 4) JS globals
    href = await js(page, r"""(()=>{try{const s=JSON.stringify(window.__NEXT_DATA__||{});
        const m=s.match(/https:\/\/services\.sheerid\.com\/verify\/[^"\\]+/);return m?m[0]:'';}catch(e){return '';}})()""", "")
    if href:
        return href.replace("\\/", "/")
    # 5) iframes
    for f in page.frames:
        if _is_sheerid(f.url):
            return f.url
    return ""


async def find_verify_button(page, wait_ready: float = 60.0):
    """Wait out 'Checking eligibility...' (disabled) until 'Verify status' is enabled."""
    deadline = time.time() + wait_ready
    while time.time() < deadline:
        btn = page.get_by_role("button", name=re.compile(r"^\s*Verify status\s*$"))
        try:
            if await btn.count() and await btn.first.is_visible() and await btn.first.is_enabled():
                return btn.first
        except Exception:
            pass
        await asyncio.sleep(1.5)
    return None


async def wait_popup_sheerid(popup, timeout: float = 25.0) -> str:
    """The popup starts at about:blank then redirects; poll until it is SheerID."""
    try:
        await popup.wait_for_load_state("domcontentloaded", timeout=int(timeout * 1000))
    except Exception:
        pass
    end = time.time() + timeout
    while time.time() < end:
        if _is_sheerid(popup.url):
            return popup.url
        await asyncio.sleep(0.5)
    return popup.url or ""


# ───────────────────────────────────────────── about-you handling

async def fill_about_you(page) -> bool:
    """Handle the age/name gate (auth.openai.com/about-you).

    OpenAI uses TWO shapes for this gate; detect fields, don't assume:
      A) 'How old are you?'  -> Full name (text) + Age (number)
      B) 'Lets confirm your age' -> Full name (text) + Birthday (date / mm-dd-yyyy)
    """
    if not await has(page, "input[name=name]"):
        return False

    name = f"{random.choice(['James', 'Robert', 'John', 'Michael', 'David'])} " \
           f"{random.choice(['Miller', 'Smith', 'Johnson', 'Williams', 'Brown'])}"
    yr = random.randint(1975, 1995)
    age = 2026 - yr
    dob = f"{random.randint(1, 12):02d}/{random.randint(1, 28):02d}/{yr}"

    await type_into(page, "input[name=name]", name, "name")
    await asyncio.sleep(0.5)

    age_sel = None
    for sel in ("input[name=age]", "input[id*=age i]", "input[type=number]"):
        if await has(page, sel):
            age_sel = sel
            break

    filled = False
    if age_sel:
        filled = await type_into(page, age_sel, str(age), "age")
    else:
        bsel = None
        for sel in ("input[name=birthday]", "input[name=birthDate]", "input[id*=birth i]",
                    "input[placeholder*=dd i]", "input[placeholder*=MM i]", "input[type=date]"):
            if await has(page, sel):
                bsel = sel
                break
        if bsel is None:
            # second visible non-hidden input (first is the name field)
            bsel = await js(page, """(()=>{const els=[...document.querySelectorAll('input')].filter(e=>{
                const t=(e.type||'').toLowerCase(); const r=e.getBoundingClientRect();
                return !['hidden','submit','checkbox','radio','file'].includes(t) && r.width>0;});
                const b=els[1]; if(!b) return null;
                return b.name ? ('input[name="'+b.name+'"]') : (b.id ? ('#'+b.id) : null);})()""", None)
        if bsel:
            is_date = await js(page, f"(()=>{{const e=document.querySelector({json.dumps(bsel)});"
                                     f"return e?(e.type==='date'):false;}})()", False)
            if is_date:
                iso = f"{yr:04d}-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"
                try:
                    await page.locator(bsel).first.fill(iso, timeout=4000)
                    filled = True
                    print(f"      [about-you] birthday(date) => {iso}", flush=True)
                except Exception as e:  # noqa: BLE001
                    print(f"      [about-you] date fill failed: {e}", flush=True)
            else:
                filled = await type_into(page, bsel, dob, "birthday")
                if not filled:
                    # segmented / masked inputs: digits only
                    el = page.locator(bsel).first
                    try:
                        await el.click(timeout=4000)
                        await page.keyboard.press("Control+A")
                        await page.keyboard.press("Delete")
                        await el.press_sequentially(dob.replace("/", ""), delay=70)
                        got = await el.input_value()
                        filled = re.sub(r"\D", "", got) == dob.replace("/", "")
                        print(f"      [about-you] birthday(digits) => {got!r} ok={filled}", flush=True)
                    except Exception:
                        pass

    print(f"      [about-you] name={name!r} age={age} dob={dob!r} filled={filled}", flush=True)
    await asyncio.sleep(0.8)
    ok = await click_continue(page)
    if not ok:
        btns = await js(page, """JSON.stringify([...document.querySelectorAll('button')]
            .map(b=>b.innerText.trim()).filter(Boolean))""", "[]")
        print(f"      [about-you] buttons={btns}", flush=True)
        # last resort: real click on the last enabled button
        try:
            loc = page.locator("button:not([disabled])")
            n = await loc.count()
            if n:
                await loc.nth(n - 1).click(timeout=5000)
                ok = True
        except Exception:
            ok = False
    print(f"      [about-you] Continue clicked={ok}", flush=True)

    # verify we left the page (validation passed)
    for _ in range(10):
        await asyncio.sleep(1)
        u = await live_url(page)
        if "about-you" not in u:
            print(f"      [about-you] left page -> {u[:60]}", flush=True)
            break
        err = await js(page, """(()=>{const e=document.querySelector('[role=alert],.text-red-500,[class*=error]');
            return e?e.innerText.slice(0,80):'';})()""", "")
        if err:
            print(f"      [about-you] validation error: {err!r}", flush=True)
    return True


# ───────────────────────────────────────────── session capture

async def capture_session(page) -> dict:
    """Read the ChatGPT OAuth session IN-PLACE on the live page.

    fetch() /api/auth/session from the page that signed in (a fresh tab can show
    'Your session has ended').
    """
    try:
        raw = await page.evaluate("""
            (async () => {
              try {
                const r = await fetch('/api/auth/session', { credentials: 'include' });
                return await r.text();
              } catch (e) { return JSON.stringify({ error: String(e) }); }
            })()
        """)
        sess = json.loads(raw) if isinstance(raw, str) and raw.strip().startswith("{") else {}
    except Exception as e:  # noqa: BLE001
        sess = {"error": str(e)}

    # refresh token from cookies
    try:
        for c in await page.context.cookies():
            if c.get("name") in SESSION_COOKIES:
                sess["refreshToken"] = c.get("value")
    except Exception:
        pass
    return sess


def save_session(sess: dict, email: str) -> None:
    if not sess or not sess.get("accessToken"):
        print(f"[session] none ({sess.get('error', 'no token')}) — skip", flush=True)
        return
    sess["email"] = sess.get("userEmail") or email
    try:
        data = json.loads(SESSIONS.read_text()) if SESSIONS.exists() else []
        if not isinstance(data, list):
            data = []
    except Exception:
        data = []
    data.append(sess)
    SESSIONS.parent.mkdir(parents=True, exist_ok=True)
    SESSIONS.write_text(json.dumps(data, indent=2))
    print(f"[session] saved -> {SESSIONS} (plan={sess.get('planType')})", flush=True)


# ───────────────────────────────────────────── page tracking

async def pick_live_page(context, current, *substrs):
    """Page whose live URL matches a substring (newest wins); else a still-open page."""
    best = None
    for p in list(context.pages):
        if p.is_closed():
            continue
        u = p.url or ""
        if any(s in u for s in substrs):
            best = p
    if best is not None:
        return best
    if current is not None and not current.is_closed():
        return current
    for p in context.pages:
        if not p.is_closed():
            return p
    return current


def _dump_pages(context) -> str:
    return " | ".join(f"{(p.url or '')[:50]}" for p in context.pages if not p.is_closed())


def _proxy_dict(proxy: str | None) -> dict | None:
    if not proxy:
        return None
    u = urlparse(proxy if "://" in proxy else f"http://{proxy}")
    d = {"server": f"{u.scheme}://{u.hostname}:{u.port}" if u.port else f"{u.scheme}://{u.hostname}"}
    if u.username:
        d["username"] = u.username
    if u.password:
        d["password"] = u.password
    return d


# ───────────────────────────────────────────── main

def gen_password() -> str:
    return "TeacherK12!" + "".join(random.choices(string.ascii_letters + string.digits, k=10)) + "#2026"


def datetime_now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


async def run_flow(headless: bool = False, proxy: str | None = None) -> dict:
    print("=" * 60, flush=True)
    print("  CHATGPT K-12 (Camoufox / Playwright)", flush=True)
    print("=" * 60, flush=True)

    if MAIL_PROVIDER == "relay" and (not SUPABASE_URL or not MAIL_KEY):
        return {"success": False, "error": "mail_relay_unconfigured"}

    print(f"[1/6] Creating mailbox (provider={MAIL_PROVIDER})…", flush=True)
    mail = create_mailbox()
    email, jwt = mail["address"], (mail.get("jwt") or mail.get("token"))
    print(f"      [+] {email}", flush=True)
    password = gen_password()
    print(f"[2/6] Password: {password}", flush=True)

    print("[3/6] Launching Camoufox (geoip + humanize, os=windows)…", flush=True)
    if not headless and not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        print("      [!] no DISPLAY found; headed Firefox will fail. "
              "Use --headless (Xvfb 'virtual') or run under a desktop / xvfb-run.", flush=True)

    result = {"success": False, "email": email, "password": password}
    kwargs = dict(
        headless="virtual" if headless else False,  # 'virtual' = Xvfb, avoids headless leaks
        geoip=True,
        humanize=True,
        os="windows",
    )
    pd = _proxy_dict(proxy)
    if pd:
        kwargs["proxy"] = pd

    async with AsyncCamoufox(**kwargs) as browser:
        page = await browser.new_page()
        context = page.context
        opened_urls: list[str] = []  # every popup/new page URL we see
        context.on("page", lambda p: opened_urls.append(p.url))

        await page.goto(f"{CHATGPT}/auth/login/?next=%2Fk12-verification", wait_until="domcontentloaded")
        await asyncio.sleep(6)

        # ---- email
        if not await fill_first_input(page, ['input[type=email]', 'input[name=email]',
                                             'input[autocomplete=email]'], email):
            result["error"] = "email_input_not_found"
            return result
        await asyncio.sleep(1)
        await click_continue(page)
        await asyncio.sleep(4)

        # ---- password (account creation): fill every empty password input
        if await has(page, "input[type=password]"):
            pw_loc = page.locator("input[type=password]")
            n_pw = await pw_loc.count()
            for idx in range(n_pw or 1):
                el = pw_loc.nth(idx)
                try:
                    if await el.is_visible():
                        await el.click(timeout=4000)
                        await el.fill(password, timeout=4000)
                        if (await el.input_value()) != password:
                            await page.keyboard.press("Control+A")
                            await page.keyboard.press("Delete")
                            await el.press_sequentially(password, delay=60)
                        print(f"      [type] password[{idx}] ok="
                              f"{(await el.input_value()) == password}", flush=True)
                except Exception as e:  # noqa: BLE001
                    print(f"      [type] password[{idx}] error: {e}", flush=True)
            await asyncio.sleep(1)
            await click_continue(page)
            await asyncio.sleep(4)

        # ---- OTP: wait for mail, find the field, fill, VERIFY, then submit.
        print("[4/6] Waiting for OTP from relay…", flush=True)
        otp = await wait_for_otp(jwt, timeout=180)
        if otp:
            otp_sel = None
            cands = ['input[autocomplete=one-time-code]', 'input[name=code]', 'input[name=otp]',
                     'input[inputmode=numeric]', 'input[type=number]', 'input[type=tel]']
            for _ in range(25):
                for s in cands:
                    if await visible_first(page, s) is not None:
                        otp_sel = s
                        break
                if otp_sel:
                    break
                await asyncio.sleep(1)
            if not otp_sel:
                otp_sel = "input[type=text],input[type=number],input[type=tel]"

            typed = False
            for attempt in range(4):
                typed = await type_into(page, otp_sel, otp, "otp")
                if typed:
                    break
                print(f"      [otp] attempt {attempt + 1}: field not holding code, retrying…", flush=True)
                await asyncio.sleep(1)

            if not typed:
                print("      ✗ OTP field could not be filled — NOT submitting "
                      "(avoids the empty-code error)", flush=True)
            else:
                print(f"      [+] OTP typed ok ({otp}); submitting…", flush=True)
                await asyncio.sleep(0.5)
                await click_continue(page)

            # wait until we leave the OTP screen (code consumed)
            consumed = False
            for i in range(40):
                await asyncio.sleep(2)
                still = await js(page, """(()=>{const e=[...document.querySelectorAll('input')]
                    .find(x=>x.autocomplete==='one-time-code'||['code','otp'].includes(x.name)
                    || (x.getAttribute('aria-label')||'').toLowerCase().includes('code'));
                    return !!e;})()""", False)
                url = await live_url(page)
                if not still or "email-verification" not in url:
                    consumed = True
                    break
                if i % 6 == 5:  # retry submit in case it didn't register
                    await click_continue(page)
            print(f"      [otp] consumed={consumed} url={(await live_url(page))[:60]}", flush=True)
        else:
            print("      [!] OTP not found", flush=True)
        await asyncio.sleep(6)
        print(f"      [pages after OTP] {_dump_pages(context)}", flush=True)

        # Re-acquire the live page (signup may have opened another tab).
        page = await pick_live_page(context, page, "about-you", "k12-verification",
                                    "auth.openai.com", "chatgpt.com")
        # collapse to one page so the state machine stays in sync
        closed = 0
        for p in list(context.pages):
            if p is not page and not p.is_closed():
                try:
                    await p.close()
                    closed += 1
                except Exception:
                    pass
        if closed:
            print(f"      [tabs] closed {closed} stale tab(s)", flush=True)
        print(f"      [about-you] live page url={(await live_url(page))[:70]}", flush=True)

        # capture session IN-PLACE (no navigation — that caused 'session ended')
        for _ in range(8):
            s = await capture_session(page)
            if s.get("accessToken"):
                save_session(s, email)
                break
            await asyncio.sleep(3)
        else:
            print("      [!] session not captured yet (will retry after verify)", flush=True)

        # ---- about-you
        for i in range(40):
            if await has(page, "input[name=name]"):
                print(f"      [about-you] form found (loop {i}) url={(await live_url(page))[:60]}", flush=True)
                await fill_about_you(page)
                break
            _u = await live_url(page)
            if "k12-verification" in _u or "chatgpt.com" in _u:
                print(f"      [k12] page reached (loop {i})", flush=True)
                break
            if i in (8, 20):
                print(f"      [wait i={i}] url={_u[:70]}", flush=True)
            await asyncio.sleep(1)

        # ---- K-12 verification page
        print("[5/6] Driving K-12 verification page…", flush=True)
        page = await pick_live_page(context, page, "k12-verification", "chatgpt.com")
        sheerid = None

        for round_ in range(8):
            _u = await live_url(page)
            if _is_sheerid(_u):
                sheerid = _u
                break
            # PRIMARY: read the SheerID anchor/href straight from the DOM
            href = await find_sheerid_anchor(page)
            if _is_sheerid(href):
                sheerid = href
                print(f"      [+] SheerID from DOM: {sheerid[:80]}", flush=True)
                break

            btn = await find_verify_button(page, wait_ready=60)
            if btn is not None:
                try:
                    # native popup capture (replaces the window.open JS hook)
                    async with page.expect_popup(timeout=30000) as pinfo:
                        await btn.click(timeout=15000)
                    popup = await pinfo.value
                    print(f"      [click] Verify status #{round_ + 1} -> popup opened", flush=True)
                    pu = await wait_popup_sheerid(popup)
                    if _is_sheerid(pu):
                        sheerid = pu
                        print(f"      [+] SheerID from popup: {sheerid[:80]}", flush=True)
                        break
                    print(f"      [popup] url={pu[:70]}", flush=True)
                except PWTimeout:
                    print(f"      [click] Verify status #{round_ + 1} (no popup event)", flush=True)
                except PWError as e:
                    print(f"      [click] Verify status #{round_ + 1} error: {str(e)[:100]}", flush=True)

            # no popup event: SheerID may have loaded in-page, as an anchor, or a stray page
            for _ in range(12):
                await asyncio.sleep(1.5)
                for u in [p.url for p in context.pages if not p.is_closed()] + opened_urls:
                    if _is_sheerid(u):
                        sheerid = u
                        print(f"      [+] SheerID from page list: {sheerid[:80]}", flush=True)
                        break
                if sheerid:
                    break
                href = await find_sheerid_anchor(page)
                if _is_sheerid(href):
                    sheerid = href
                    break
            if sheerid:
                break
            print(f"      [verify round {round_ + 1}] url={(await live_url(page))[:70]} "
                  f"opened={opened_urls}", flush=True)

        if sheerid:
            print(f"      [+] SheerID URL: {sheerid[:90]}", flush=True)
            result["sheerid_url"] = sheerid
            try:
                sys.path.insert(0, str(K12_DIR))
                from script import K12Verifier  # type: ignore
                res = await asyncio.to_thread(
                    lambda: K12Verifier(sheerid, use_temp_email=True, manual_email=email).verify())
                result["verify"] = res
                result["success"] = bool(res.get("success"))
                print(f"      K12Verifier: success={result['success']}", flush=True)
            except Exception as e:  # noqa: BLE001
                result["verify_error"] = str(e)
                print(f"      K12Verifier error: {e}", flush=True)
            s2 = await capture_session(page)
            if s2.get("accessToken"):
                save_session(s2, email)
        else:
            result["error"] = f"no_sheerid_url (url={(await live_url(page))[:80]})"

        CREATED.parent.mkdir(parents=True, exist_ok=True)
        with CREATED.open("a", encoding="utf-8") as f:
            f.write(f"{email}----{password}----{datetime_now()}\n")
        print(f"\n[+] creds -> {CREATED}", flush=True)
        return result


def main() -> int:
    ap = argparse.ArgumentParser(description="ChatGPT K-12 flow on Camoufox (Firefox anti-detect)")
    ap.add_argument("--headless", action="store_true",
                    help="run under Xvfb ('virtual' headless) instead of a visible window")
    ap.add_argument("--proxy", default=None, help="http(s)/socks5 proxy URL; geoip follows its exit IP")
    a = ap.parse_args()
    r = asyncio.run(run_flow(headless=a.headless, proxy=a.proxy))
    print(json.dumps(r, indent=2, default=str))
    return 0 if r.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
