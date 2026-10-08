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

Mail Providers & Custom Domain Setup:
  * K12_MAIL_PROVIDER='relay' (default):
    Uses our self-hosted Supabase relay (/functions/v1/temp-mail-api) backed by
    Cloudflare Email Routing. To plug in a real (non-disposable / school) domain:
      export K12_MAIL_PROVIDER=relay
      export K12_DOMAINS="school.edu.pl"    # or export K12_CUSTOM_DOMAIN="school.edu.pl"
    or pass --domain school.edu.pl on the CLI.
  * K12_MAIL_PROVIDER='mailtm':
    Uses public mail.tm API (maxxspace.com) for testing non-gated steps.
  * K12_MAIL_PROVIDER='school':
    Uses the REAL school mailbox (binus.ac.id / Microsoft 365) — the address is
    a plus-address of SCHOOL_EMAIL, e.g. raymondi+oct1@binus.ac.id (--index N
    picks N). OpenAI accepts binus.ac.id as a school domain, so this is the
    provider that gets past the "register with a school email address" gate.
    No mailbox is created over HTTP: the OTP is read from the already-logged-in
    M365 session by shelling out to scripts/school_mail_browser.py (nodriver),
    which re-uses the persisted profile ~/.config/auto-freecf/school-profile.

Sessions are captured from /api/auth/session and saved for 9Router.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import re
import string
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from camoufox.async_api import AsyncCamoufox
from playwright.async_api import Error as PWError
from playwright.async_api import TimeoutError as PWTimeout

try:
    from gateway_session import apply_gateway_session, is_gateway
except ImportError:
    try:
        from scripts.gateway_session import apply_gateway_session, is_gateway
    except Exception:
        def is_gateway(p=None): return False
        async def apply_gateway_session(p, s=None): return False

# Shared Playwright page helpers (single home: scripts/camoufox_helpers.py)
try:
    from camoufox_helpers import (fill_first_input, has, js, live_url, type_into,
                                  visible_first)
except ImportError:
    from scripts.camoufox_helpers import (fill_first_input, has, js, live_url, type_into,
                                          visible_first)

HOME = Path.home()
AUTO_FREECF = HOME / "Auto-FreeCF"
K12_DIR = HOME / "petani-proxy" / "Farm-Acc-ChatGPT-K-12-Teachers" / "PyRuntime_64"
SESSIONS = K12_DIR / "k12_sessions.json"
CREATED = K12_DIR / "created_k12_accounts.txt"

CHATGPT = "https://chatgpt.com"

BUTTON_LABELS = ("Continue", "Next", "Sign up", "Verify", "Submit", "Log in",
                 "Finish creating account", "Create account", "Finish")
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


# Pluggable mail provider: K12_MAIL_PROVIDER = 'relay' (default) | 'mailtm' | 'school'
MAIL_PROVIDER = (os.environ.get("K12_MAIL_PROVIDER") or _ENV.get("K12_MAIL_PROVIDER") or "relay").strip().lower()
MAILTM_BASE = "https://api.mail.tm"
MAILTM_DOMAIN = "maxxspace.com"  # the only mail.tm domain
_MAILTM_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# ── school mailbox (real binus.ac.id / M365) ─────────────────────────────
# The signup address is a plus-address of SCHOOL_EMAIL: raymondi+oct1@binus.ac.id.
# Everything lands in the one real inbox; the +oct<N> tag just keeps each run's
# thread separable. Reading the OTP re-uses scripts/school_mail_browser.py
# (nodriver + persisted profile ~/.config/auto-freecf/school-profile).
SCHOOL_MAIL_SCRIPT = AUTO_FREECF / "scripts" / "school_mail_browser.py"
SCHOOL_PROFILE_DIR = HOME / ".config" / "auto-freecf" / "school-profile"
# school_mail_browser.py needs nodriver. It is now installed in the camoufox venv
# too, so re-use THIS interpreter; fall back to the managed venv for older setups.
MANAGED_VENV_PY = HOME / ".local" / "share" / "auto-freecf" / "venv" / "bin" / "python"

def school_email_default() -> str:
    """SCHOOL_EMAIL from the environment or ~/.config/auto-freecf/.env."""
    return (os.environ.get("SCHOOL_EMAIL") or _ENV.get("SCHOOL_EMAIL") or "").strip()

def school_address(base_email: str, index: int = 1) -> str:
    """raymondi@binus.ac.id + index 1 -> raymondi+oct1@binus.ac.id"""
    base = (base_email or "").strip()
    if "@" not in base:
        return ""
    local, _, dom = base.partition("@")
    local = local.split("+", 1)[0]  # never stack tags on an already-tagged address
    return f"{local}+oct{int(index)}@{dom}"

def _school_mail_python() -> str:
    """Interpreter that can import nodriver (camoufox venv preferred)."""
    if _can_import(sys.executable, "nodriver"):
        return sys.executable
    if MANAGED_VENV_PY.exists() and os.access(str(MANAGED_VENV_PY), os.X_OK):
        return str(MANAGED_VENV_PY)
    return sys.executable


def _can_import(python: str, module: str) -> bool:
    try:
        return subprocess.run([python, "-c", f"import {module}"],
                              capture_output=True).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def _relay_create_mailbox(custom_domain: str | None = None) -> dict:
    dom = custom_domain or os.environ.get("K12_CUSTOM_DOMAIN") or _ENV.get("K12_CUSTOM_DOMAIN")
    if not dom:
        valid_domains = [d.strip() for d in DOMAINS if d.strip()]
        dom = random.choice(valid_domains) if valid_domains else "kancalabs.biz.id"
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


def _req(method: str, url: str, *, retries: int = 4, **kw):
    """HTTP request with retry/backoff for transient timeouts (fresh WARP tunnels
    reset/fail the first connection)."""
    last = None
    for i in range(retries):
        try:
            r = requests.request(method, url, timeout=kw.pop("timeout", 30), **kw)
            return r
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last


def _mailtm_create_mailbox() -> dict:
    """Register a random address on mail.tm and fetch its JWT."""
    local = "k" + "".join(random.choices(string.ascii_lowercase + string.digits, k=11))
    address = f"{local}@{MAILTM_DOMAIN}"
    pw = "".join(random.choices(string.ascii_letters + string.digits, k=16))
    r = _req("POST", f"{MAILTM_BASE}/accounts", json={"address": address, "password": pw},
             headers=_MAILTM_HEADERS, timeout=30)
    r.raise_for_status()
    r = _req("POST", f"{MAILTM_BASE}/token", json={"address": address, "password": pw},
             headers=_MAILTM_HEADERS, timeout=30)
    r.raise_for_status()
    return {"address": address, "token": r.json()["token"], "provider": "mailtm"}


def _mailtm_poll_mail(token: str) -> list[dict]:
    """List messages, fetch each in full, normalise to the keys wait_for_otp expects."""
    h = {**_MAILTM_HEADERS, "Authorization": f"Bearer {token}"}
    r = _req("GET", f"{MAILTM_BASE}/messages", headers=h, timeout=30)
    r.raise_for_status()
    d = r.json()
    items = d if isinstance(d, list) else (d.get("hydra:member") or d.get("member") or [])
    out = []
    for it in items:
        full = it
        try:
            fr = _req("GET", f"{MAILTM_BASE}/messages/{it['id']}", headers=h, timeout=30)
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


def create_mailbox(provider: str | None = None, domain: str | None = None,
                   index: int = 1, school_email: str | None = None) -> dict:
    prov = (provider or MAIL_PROVIDER or "relay").strip().lower()
    if prov == "school":
        # Nothing to create — the mailbox already exists. Mint the plus-address.
        base = school_email or school_email_default()
        if not base:
            raise RuntimeError(
                "school provider needs a school address — set SCHOOL_EMAIL in "
                "~/.config/auto-freecf/.env or pass --school-email"
            )
        addr = school_address(base, index)
        if not addr:
            raise RuntimeError(f"malformed school email: {base!r}")
        return {"address": addr, "provider": "school", "base": base, "index": index}
    if prov == "mailtm":
        return _mailtm_create_mailbox()
    return _relay_create_mailbox(domain)


def poll_mail(jwt: str, provider: str | None = None) -> list[dict]:
    prov = (provider or MAIL_PROVIDER or "relay").strip().lower()
    if prov == "school":
        # The school mailbox is read through a browser subprocess, not HTTP.
        # See school_otp_via_subprocess(); this keeps the relay/mailtm signature.
        return []
    if prov == "mailtm":
        return _mailtm_poll_mail(jwt)
    return _relay_poll_mail(jwt)


def school_otp_via_subprocess(timeout: int = 180, cwd: Path | None = None) -> str | None:
    """Read the next OpenAI OTP from the real school mailbox.

    Shells out to scripts/school_mail_browser.py, which drives a nodriver Chrome
    against the PERSISTED profile (~/.config/auto-freecf/school-profile). That
    session must already be logged in — run `kancahub mail test` (or
    `school_mail_browser.py login`) once by hand to establish it, and re-run it
    whenever Microsoft expires it (MFA cannot be completed unattended).
    The child prints `[school] OTP ...: <code>` and we scrape the 6 digits back.
    """
    if not SCHOOL_MAIL_SCRIPT.exists():
        print(f"      ✗ school_mail_browser.py not found at {SCHOOL_MAIL_SCRIPT}", flush=True)
        return None

    py = _school_mail_python()
    cmd = [py, str(SCHOOL_MAIL_SCRIPT), "otp", "--timeout", str(int(timeout))]
    print(f"      [school] $ {' '.join(cmd)}", flush=True)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd or SCHOOL_MAIL_SCRIPT.parent),
            capture_output=True, text=True, timeout=timeout + 120,
        )
    except subprocess.TimeoutExpired:
        print("      ✗ school mailbox reader timed out", flush=True)
        return None
    except Exception as e:  # noqa: BLE001
        print(f"      ✗ school mailbox reader failed: {e}", flush=True)
        return None

    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    for line in out.splitlines():
        if "otp" in line.lower():
            print(f"      {line.strip()}", flush=True)
    # child prints: "[school] OTP from subject: 123456"
    m = re.search(r"OTP[^0-9]{0,40}(\d{6})", out)
    if m:
        return m.group(1)
    if proc.returncode != 0:
        print(f"      ✗ school mailbox reader exit={proc.returncode} "
              f"(is the M365 session still logged in? run: kancahub mail test)", flush=True)
    return None


def _blob(m: dict) -> str:
    return " ".join(str(m.get(k, "")) for k in ("subject", "text", "body", "html", "snippet", "from"))


async def wait_for_otp(jwt: str, timeout: int = 180, provider: str | None = None) -> str | None:
    prov = (provider or MAIL_PROVIDER or "relay").strip().lower()
    if prov == "school":
        print(f"      [school] reading OTP from the real M365 inbox (max {timeout}s)…", flush=True)
        return await asyncio.to_thread(school_otp_via_subprocess, timeout)
    print(f"      [mail] polling relay for OTP (max {timeout}s)…", flush=True)
    start = time.time()
    seen = set()
    while time.time() - start < timeout:
        try:
            for m in await asyncio.to_thread(poll_mail, jwt, provider):
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


# playwright helpers (js/has/visible_first/type_into/fill_first_input) live in
# scripts/camoufox_helpers.py and are imported above.

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

async def get_about_you_error(page) -> str:
    """Extract any error alert, validation warning, or Terms of Use rejection message."""
    return await js(page, """(()=>{
        // 1. Explicit error/alert containers
        const selectors = [
            '[role=alert]',
            'div[data-testid*=error i]',
            'div[class*=error i]',
            'p[class*=error i]',
            'span[class*=error i]',
            '.text-red-500',
            '.text-danger',
            '[class*=banner i]'
        ];
        for (const s of selectors) {
            const els = document.querySelectorAll(s);
            for (const el of els) {
                const t = (el.innerText || '').trim();
                if (t && t.length > 2) return t;
            }
        }
        // 2. Fallback: inspect full body text for known OpenAI rejection messages
        const text = (document.body ? document.body.innerText : '') || '';
        const phrases = [
            "We can't create your account due to our Terms of Use",
            "We cannot create your account due to our Terms of Use",
            "Terms of Use",
            "can't create your account",
            "cannot create your account",
            "unable to create your account",
            "Enter a valid age",
            "Something went wrong"
        ];
        for (const p of phrases) {
            const idx = text.toLowerCase().indexOf(p.toLowerCase());
            if (idx !== -1) {
                const start = Math.max(0, idx);
                const end = Math.min(text.length, idx + 120);
                const line = text.slice(start, end).split('\\n')[0];
                return line.trim();
            }
        }
        return '';
    })()""", "") or ""


async def fill_about_you(page) -> tuple[bool, str]:
    """Handle the age/name gate (auth.openai.com/about-you).

    OpenAI uses TWO shapes for this gate; detect fields, don't assume:
      A) 'How old are you?'  -> Full name (text) + Age (number)
      B) 'Lets confirm your age' -> Full name (text) + Birthday (date / mm-dd-yyyy)

    Returns:
        (success: bool, error_message: str)
    """
    if not await has(page, "input[name=name]"):
        return False, "no_name_input"

    # Pre-submit check: surface any existing alert already present
    initial_err = await get_about_you_error(page)
    if initial_err:
        print(f"      [about-you] pre-submit page alert: {initial_err!r}", flush=True)

    name = f"{random.choice(['James', 'Robert', 'John', 'Michael', 'David'])} " \
           f"{random.choice(['Miller', 'Smith', 'Johnson', 'Williams', 'Brown'])}"
    yr = random.randint(1975, 1995)
    age = 2026 - yr
    dob = f"{random.randint(1, 12):02d}/{random.randint(1, 28):02d}/{yr}"

    await type_into(page, "input[name=name]", name, "name")
    await asyncio.sleep(0.5)

    age_sel = None
    bsel = None
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

    # GATE: don't click until the required field really holds a value, else the
    # form shows 'Enter a valid age to continue' and we never advance.
    want_sel = age_sel or bsel
    if want_sel:
        for attempt in range(4):
            cur = await js(page, f"(()=>{{const e=document.querySelector({json.dumps(want_sel)});return e?e.value:'';}})()", "") or ""
            if str(cur).strip():
                break
            print(f"      [about-you] field empty (attempt {attempt+1}); refilling…", flush=True)
            filled = await type_into(page, want_sel, str(age) if want_sel == age_sel else dob, "about-you-retry")
            await asyncio.sleep(0.6)
        final = await js(page, f"(()=>{{const e=document.querySelector({json.dumps(want_sel)});return e?e.value:'';}})()", "") or ""
        print(f"      [about-you] final field value={final!r}", flush=True)

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
    print(f"      [about-you] Continue / Finish clicked={ok}", flush=True)

    # Post-submit verification: poll to confirm we leave the page OR catch error alerts
    for _ in range(12):
        await asyncio.sleep(1)
        u = await live_url(page)
        if "about-you" not in u:
            print(f"      [about-you] successfully left page -> {u[:60]}", flush=True)
            return True, ""
        err = await get_about_you_error(page)
        if err:
            print(f"      [about-you] error alert detected: {err!r}", flush=True)
            if any(k in err.lower() for k in ("terms of use", "can't create your account", "cannot create", "unable to create")):
                print(f"      [!] Terms-of-Use rejection from OpenAI: {err!r}", flush=True)
                return False, f"terms_of_use_blocked: {err}"

    # If still on about-you, check and surface the exact alert
    final_err = await get_about_you_error(page)
    if "about-you" in await live_url(page):
        msg = final_err or "stayed_on_about_you_page"
        print(f"      [!] Did not advance from about-you page: {msg!r}", flush=True)
        return False, msg

    return True, ""


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


async def run_flow(
    headless: bool = False,
    proxy: str | None = None,
    mail_provider: str | None = None,
    domain: str | None = None,
    index: int = 1,
    school_email: str | None = None,
) -> dict:
    print("=" * 60, flush=True)
    print("  CHATGPT K-12 (Camoufox / Playwright)", flush=True)
    print("=" * 60, flush=True)

    prov = (mail_provider or MAIL_PROVIDER or "relay").strip().lower()
    if prov == "relay" and (not SUPABASE_URL or not MAIL_KEY):
        return {"success": False, "error": "mail_relay_unconfigured"}
    if prov == "school":
        base = school_email or school_email_default()
        if not base:
            return {"success": False, "error": "school_email_unset",
                    "hint": "set SCHOOL_EMAIL in ~/.config/auto-freecf/.env or pass --school-email"}
        if not SCHOOL_PROFILE_DIR.is_dir():
            print(f"      [!] {SCHOOL_PROFILE_DIR} missing — the OTP reader will need an "
                  f"interactive M365 login first (run: kancahub mail test)", flush=True)

    print(f"[1/6] Creating mailbox (provider={prov}, domain={domain or 'auto'}"
          f"{f', index={index}' if prov == 'school' else ''})…", flush=True)
    mail = create_mailbox(provider=prov, domain=domain, index=index, school_email=school_email)
    email, jwt = mail["address"], (mail.get("jwt") or mail.get("token"))
    print(f"      [+] {email}", flush=True)
    if prov == "school":
        print(f"      [+] school mailbox (base={mail.get('base')}); OTP read from the "
              f"persisted M365 profile", flush=True)
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
        if is_gateway(proxy):
            await apply_gateway_session(context, email)
            await apply_gateway_session(page, email)
            print(f"      📌 Sticky session applied (id={email})", flush=True)
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
        print(f"[4/6] Waiting for OTP from "
              f"{'the school mailbox (binus.ac.id)' if prov == 'school' else 'relay'}…", flush=True)
        otp = await wait_for_otp(jwt, timeout=180, provider=prov)
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
        about_you_passed = False
        about_you_error = ""
        for i in range(40):
            if await has(page, "input[name=name]"):
                print(f"      [about-you] form found (loop {i}) url={(await live_url(page))[:60]}", flush=True)
                about_ok, about_err = await fill_about_you(page)
                if not about_ok:
                    about_you_error = about_err
                else:
                    about_you_passed = True
                break
            _u = await live_url(page)
            if "k12-verification" in _u or "chatgpt.com" in _u:
                print(f"      [k12] page reached (loop {i})", flush=True)
                about_you_passed = True
                break
            if i in (8, 20):
                print(f"      [wait i={i}] url={_u[:70]}", flush=True)
            await asyncio.sleep(1)

        # Check if account creation failed at about-you (e.g. Terms of Use rejection)
        _curr_u = await live_url(page)
        if "about-you" in _curr_u or about_you_error:
            active_err = about_you_error or (await get_about_you_error(page)) or "terms_of_use_blocked"
            print(f"\n[!] STOPPING: OpenAI blocked account creation at about-you: {active_err!r}", flush=True)
            print("      (Aborting early — will not retry K-12 verification rounds on blocked account)", flush=True)
            result["error"] = active_err
            CREATED.parent.mkdir(parents=True, exist_ok=True)
            with CREATED.open("a", encoding="utf-8") as f:
                f.write(f"{email}----{password}----{datetime_now()}----BLOCKED:{active_err}\n")
            print(f"[+] blocked credentials logged -> {CREATED}", flush=True)
            return result

        # ---- K-12 verification page
        print("[5/6] Driving K-12 verification page…", flush=True)
        page = await pick_live_page(context, page, "k12-verification", "chatgpt.com")
        sheerid = None

        for round_ in range(8):
            _u = await live_url(page)
            if "about-you" in _u:
                err = await get_about_you_error(page)
                print(f"      [!] Flow still on about-you (alert: {err!r}); aborting verify rounds.", flush=True)
                result["error"] = err or "blocked_at_about_you"
                break
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
    ap = argparse.ArgumentParser(
        description="ChatGPT K-12 flow on Camoufox (Firefox anti-detect)",
        epilog=(
            "Providers:\n"
            "  relay   (default) Supabase temp-mail relay with K12_DOMAINS\n"
            "  mailtm  public mail.tm (disposable — OpenAI blocks it at signup)\n"
            "  school  REAL binus.ac.id M365 mailbox via plus-addressing\n"
            "\n"
            "School provider example:\n"
            "  K12_MAIL_PROVIDER=school python3 scripts/k12_camoufox.py --index 1\n"
            "  # -> raymondi+oct1@binus.ac.id ; OTP read from the persisted\n"
            "  #    M365 profile ~/.config/auto-freecf/school-profile\n"
            "  # Requires a live M365 session: run `kancahub mail test` first.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--headless", action="store_true",
                    help="run under Xvfb ('virtual' headless) instead of a visible window")
    ap.add_argument("--proxy", default=None, help="http(s)/socks5 proxy URL; geoip follows its exit IP")
    ap.add_argument("--mail-provider", choices=["relay", "mailtm", "school"], default=None,
                    help="mail backend: 'relay' (default), 'mailtm', or 'school' "
                         "(real binus.ac.id M365 inbox via plus-addressing)")
    ap.add_argument("--domain", default=None,
                    help="custom domain to request from mail relay (e.g. your school .edu.pl domain)")
    ap.add_argument("--index", type=int, default=1,
                    help="plus-address tag for the school provider: raymondi+oct<N>@binus.ac.id (default 1)")
    ap.add_argument("--school-email", default=None,
                    help="school address to plus-tag (default: SCHOOL_EMAIL env / .env)")
    a = ap.parse_args()
    r = asyncio.run(run_flow(headless=a.headless, proxy=a.proxy, mail_provider=a.mail_provider,
                             domain=a.domain, index=a.index, school_email=a.school_email))
    print(json.dumps(r, indent=2, default=str))
    return 0 if r.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
