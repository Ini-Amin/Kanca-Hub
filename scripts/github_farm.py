#!/usr/bin/env python3
"""
GitHub signup + GitHub Education (Student Pack) application — nodriver, Linux.

WHAT THIS AUTOMATES (honestly)
==============================
  ✅ NEW GitHub account signup via https://github.com/signup
       - email entry  (plus-address: raymondi+gh<N>@binus.ac.id)
       - password entry
       - unique username generation + availability retry
       - the "verify your email" **8-digit launch code** step, read live from the
         BINUS M365 school mailbox (Outlook Web) using the same browser-reader
         approach as scripts/school_mail_browser.py
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
     signup behind Arkose. nodriver is CDP-based and has NO solver here. If a
     puzzle appears we DETECT it and REPORT it — we never claim success.
  ❌ Identity / academic attestation on the Education form:
       - legal name attestation, "I am a student" checkbox, and especially the
         **photo/scan of a student ID or enrollment proof** that a human must
         provide and that GitHub reviews manually.
     We stop BEFORE anything requiring an attestation or a photo upload.
  ❌ Email domain proof for the student pack, billing, and final human review.
  ❌ MFA / device verification on the GitHub account if GitHub enforces it.

Because of the above, treat this as a *helper*, not a turnkey farmer. Expect to
finish captchas and the Education photo step by hand.

RESPONSIBILITY
==============
Use only with mailboxes and identities you are legitimately entitled to use.
Only the BINUS mailbox raymondi@binus.ac.id (plus-addressing) is targeted. This
does NOT touch the existing account 'Ini-Amin'.

CONFIG (~/.config/auto-freecf/.env) — same keys the school reader uses:
    SCHOOL_EMAIL=raymondi@binus.ac.id
    SCHOOL_MAIL_PASSWORD=...
    SCHOOL_MAIL_URL=https://outlook.office.com/mail/   # optional

CLI:
    python3 github_farm.py --check                 # deps + mailbox self-report
    python3 github_farm.py --index 1 --dry-run     # walk signup, screenshot, no submit
    python3 github_farm.py --index 1               # real signup (stops before captcha/attestation)
    python3 github_farm.py --index 1 --headless
    python3 github_farm.py --index 1 --proxy http://user:pass@host:port

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


# ─────────────────────────────────────────────────────────── nodriver helpers

def _unwrap(v):
    if isinstance(v, dict) and "type" in v and "value" in v:
        return v["value"]
    if isinstance(v, list):
        return [_unwrap(x) for x in v]
    return v


async def js(tab, expr: str, default=None):
    """Evaluate JS and unwrap nodriver's RemoteObject quirks."""
    try:
        r = await tab.evaluate(expr, return_by_value=True)
    except Exception:
        return default
    if r is not None and r.__class__.__name__ == "RemoteObject":
        v = getattr(r, "value", None)
        if v is None:
            dv = getattr(r, "deep_serialized_value", None)
            v = getattr(dv, "value", None) if dv else None
        r = v
    r = _unwrap(r)
    return default if r is None else r


async def _query_all(tab, selector: str) -> list[dict]:
    raw = await js(tab, """
        (function(){
          const out=[];
          const els=[...document.querySelectorAll(%s)];
          els.forEach((e,i)=>{ const r=e.getBoundingClientRect();
            if(r.width>0 && r.height>0) out.push({idx:i, text:(e.innerText||e.value||'').trim(),
              x:r.left+r.width/2, y:r.top+r.height/2, w:r.width, h:r.height}); });
          return JSON.stringify(out);
        })()
    """ % json.dumps(selector), "[]")
    try:
        return json.loads(raw) if isinstance(raw, str) else (raw or [])
    except Exception:
        return []


async def _cdp_click_xy(tab, x: float, y: float) -> bool:
    import nodriver as uc
    x, y = int(round(x)), int(round(y))
    if x <= 0 or y <= 0:
        return False
    btn = uc.cdp.input_.MouseButton.LEFT
    await tab.send(uc.cdp.input_.dispatch_mouse_event("mouseMoved", x=x, y=y))
    await asyncio.sleep(0.05)
    await tab.send(uc.cdp.input_.dispatch_mouse_event("mousePressed", x=x, y=y, button=btn, buttons=1, click_count=1))
    await asyncio.sleep(0.05)
    await tab.send(uc.cdp.input_.dispatch_mouse_event("mouseReleased", x=x, y=y, button=btn, buttons=0, click_count=1))
    return True


async def robust_click(tab, selector: str | None = None, text: str | None = None,
                       timeout: float = 5.0) -> bool:
    """Real CDP click on a centered element (CSS selector first, else text)."""
    el = None
    if selector:
        try:
            el = await tab.select(selector, timeout=timeout)
        except Exception:
            el = None
    if el is None and text:
        try:
            el = await tab.find(text, best_match=True, timeout=timeout)
        except Exception:
            el = None
    if el is None:
        return False
    try:
        await el.scroll_into_view()
    except Exception:
        pass
    await asyncio.sleep(0.2)
    center = None
    try:
        center = _unwrap(await el.apply(
            "function(e){const r=e.getBoundingClientRect();"
            "return {x:r.left+r.width/2,y:r.top+r.height/2,w:r.width,h:r.height};}",
            return_by_value=True))
    except Exception:
        center = None
    if not center or not center.get("w"):
        try:
            pos = await el.get_position()
            center = {"x": pos.center[0], "y": pos.center[1]}
        except Exception:
            return False
    return await _cdp_click_xy(tab, center["x"], center["y"])


async def cdp_type(tab, selector: str, value: str) -> bool:
    """Focus (real click) -> Ctrl+A/Delete -> CDP Input.insertText. React-safe."""
    import nodriver as uc
    await robust_click(tab, selector=selector, timeout=4)
    await asyncio.sleep(0.2)
    try:
        await tab.send(uc.cdp.input_.dispatch_key_event(
            "keyDown", modifiers=2, key="a", code="KeyA", windows_virtual_key_code=65))
        await tab.send(uc.cdp.input_.dispatch_key_event(
            "keyUp", modifiers=2, key="a", code="KeyA", windows_virtual_key_code=65))
        await tab.send(uc.cdp.input_.dispatch_key_event(
            "keyDown", key="Delete", code="Delete", windows_virtual_key_code=46))
        await tab.send(uc.cdp.input_.dispatch_key_event(
            "keyUp", key="Delete", code="Delete", windows_virtual_key_code=46))
    except Exception:
        pass
    await asyncio.sleep(0.15)
    try:
        await tab.send(uc.cdp.input_.insert_text(str(value)))
    except Exception:
        for ch in str(value):
            try:
                await tab.send(uc.cdp.input_.insert_text(ch))
            except Exception:
                pass
    await asyncio.sleep(0.3)
    got = await js(tab, f"(()=>{{const e=document.querySelector({json.dumps(selector)});return e?e.value:null;}})()", "")
    ok = str(got).strip() == str(value).strip()
    print(f"      [type] {selector} <- {value!r} => {ok}", flush=True)
    return ok


async def react_fill(tab, selector: str, value: str) -> bool:
    """Fill a React-controlled input via the native value setter + events."""
    expr = """
(function(){
  const s = %s, v = %s;
  const el = document.querySelector(s);
  if (!el) return false;
  el.focus();
  const proto = (el instanceof HTMLTextAreaElement) ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
  const tr = el._valueTracker; if (tr) tr.setValue('');
  if (setter) setter.call(el, v); else el.value = v;
  el.dispatchEvent(new InputEvent('input',{bubbles:true,data:v,inputType:'insertText'}));
  el.dispatchEvent(new Event('change',{bubbles:true}));
  return String(el.value||'').trim() === String(v||'').trim();
})()
""" % (json.dumps(selector), json.dumps(value))
    return bool(await js(tab, expr, False))


async def fill_first_input(tab, selectors: list[str], value: str) -> bool:
    """cdp_type into the first present selector; fall back to typing."""
    for sel in selectors:
        if await js(tab, f"!!document.querySelector({json.dumps(sel)})", False):
            if await cdp_type(tab, sel, value):
                return True
            if await react_fill(tab, sel, value):
                return True
    return False


async def screenshot(tab, name: str) -> str | None:
    try:
        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        path = SHOTS_DIR / f"{time.strftime('%H%M%S')}_{name}.png"
        await tab.save_screenshot(str(path))
        print(f"      [shot] {path}", flush=True)
        return str(path)
    except Exception as e:
        print(f"      [shot] failed: {e}", flush=True)
        return None


def _page_text(tab):
    return js(tab, "document.body ? document.body.innerText : ''", "")


async def detect_captcha(tab) -> str | None:
    """Return a description if a human-verification puzzle is present."""
    html = await js(tab, "document.documentElement ? document.documentElement.outerHTML : ''", "")
    low = (html or "").lower()
    for needle, label in (
        ("arkoselabs", "Arkose / FunCaptcha"),
        ("funcaptcha", "Arkose / FunCaptcha"),
        ("hcaptcha", "hCaptcha"),
        ("recaptcha", "reCAPTCHA"),
        ("challenge-container", "GitHub challenge iframe"),
        ("verify you are human", "human-verification prompt"),
    ):
        if needle in low:
            return label
    # visible text hint
    txt = (await _page_text(tab) or "").lower()
    if "verify" in txt and "human" in txt:
        return "human-verification prompt (text)"
    return None


# ─────────────────────────────────────────────────────────── school mailbox

async def school_login(tab, email: str, password: str) -> None:
    """Walk the Microsoft login flow (email -> password -> stay signed in)."""
    print("      [school] completing Microsoft login…", flush=True)
    for _ in range(25):
        if await js(tab, "!!document.querySelector('input[type=email],input[name=loginfmt]')", False):
            break
        await asyncio.sleep(1)
    await js(tab, """(()=>{const e=document.querySelector('input[type=email],input[name=loginfmt]');
        if(!e) return 0; e.focus();
        const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
        s.call(e,%s); e.dispatchEvent(new InputEvent('input',{bubbles:true,data:%s}));
        return 1;})()""" % (json.dumps(email), json.dumps(email)), 0)
    await asyncio.sleep(0.5)
    await _click_any(tab, ["Next", "Sign in"])
    await asyncio.sleep(4)

    for _ in range(25):
        if await js(tab, "!!document.querySelector('input[type=password],input[name=passwd]')", False):
            break
        await asyncio.sleep(1)
    await js(tab, """(()=>{const e=document.querySelector('input[type=password],input[name=passwd]');
        if(!e) return 0; e.focus();
        const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
        s.call(e,%s); e.dispatchEvent(new InputEvent('input',{bubbles:true,data:%s}));
        return 1;})()""" % (json.dumps(password), json.dumps(password)), 0)
    await asyncio.sleep(0.5)
    await _click_any(tab, ["Sign in", "Next"])
    await asyncio.sleep(6)

    for label in ("Yes", "No"):
        if await _click_any(tab, [label], timeout=4):
            print(f"      [school] answered '{label}' to stay-signed-in", flush=True)
            break
    await asyncio.sleep(6)


async def _click_any(tab, labels: list[str], timeout: float = 3.0) -> bool:
    for label in labels:
        try:
            el = await tab.find(label, best_match=True, timeout=timeout)
            if el:
                try:
                    await el.scroll_into_view()
                except Exception:
                    pass
                await el.click()
                return True
        except Exception:
            continue
    return False


async def wait_inbox(tab, timeout: float = 60) -> bool:
    for _ in range(int(timeout)):
        u = await js(tab, "location.href", "")
        if "outlook" in u and ("mail" in u or "owa" in u):
            if await js(tab, "!!document.querySelector('[role=main],[aria-label*=Message],div[role=list]')", False):
                return True
        await asyncio.sleep(1)
    return False


async def read_inbox_text(tab) -> list[str]:
    """Best-effort scrape of visible list items + page text for OTP scanning."""
    raw = await js(tab, """JSON.stringify(
        [...document.querySelectorAll('[role=option],[role=listitem],[aria-label]')]
        .map(e=>(e.innerText||'').trim())
        .filter(t=>t && t.length>2).slice(0,25))""", "[]")
    items: list[str] = []
    try:
        items = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except Exception:
        items = []
    body = await _page_text(tab)
    if body:
        items.append(body)
    return items


def _extract_launch_code(texts: list[str]) -> str | None:
    """GitHub emails an 8-digit launch code. Find it in subjects/body text."""
    joined = "\n".join(t for t in texts if t)
    # Strong anchors first (GitHub's own copy).
    for pat in (
        r'launch code[:\s]*\b(\d{8})\b',
        r'\b(\d{8})\b\s*(?:is your|as your) launch code',
        r'your GitHub launch code[^0-9]{0,40}(\d{8})',
        r'verification code[:\s]*\b(\d{8})\b',
    ):
        m = re.search(pat, joined, re.I)
        if m:
            return m.group(1)
    # Fallback: any standalone 8-digit token in a GitHub context.
    if re.search(r"github", joined, re.I):
        m = re.search(r'\b(\d{8})\b', joined)
        if m:
            return m.group(1)
    return None


async def read_launch_code(email: str, timeout: int = 240, keep_open: bool = False) -> str | None:
    """Open Outlook Web, wait for GitHub's 8-digit launch code, return it."""
    import nodriver as uc
    pw = cfg("SCHOOL_MAIL_PASSWORD")
    url = cfg("SCHOOL_MAIL_URL", SCHOOL_MAIL_URL)
    if not pw:
        print("      [school] ✗ SCHOOL_MAIL_PASSWORD not set in .env", flush=True)
        return None

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"      [school] launching mailbox browser (profile={PROFILE_DIR})", flush=True)
    br = await uc.start(headless=False, sandbox=False, user_data_dir=str(PROFILE_DIR))
    tab = await br.get(url)
    await asyncio.sleep(8)

    title = await js(tab, "document.title", "")
    cur = await js(tab, "location.href", "")
    print(f"      [school] page: {title!r} @ {cur[:70]}", flush=True)
    if any(k in cur for k in ("login.microsoftonline", "login.live", "adfs")) or "sign in" in (title or "").lower():
        await school_login(tab, email, pw)
    ok = await wait_inbox(tab, timeout=60)
    print(f"      [school] inbox ready: {ok}", flush=True)

    start = time.time()
    while time.time() - start < timeout:
        texts = await read_inbox_text(tab)
        code = _extract_launch_code(texts)
        if code:
            print(f"      [school] ✓ launch code: {code}", flush=True)
            if not keep_open:
                try:
                    br.stop()
                except Exception:
                    pass
            return code
        await asyncio.sleep(6)
    print("      [school] no launch code within timeout", flush=True)
    if not keep_open:
        try:
            br.stop()
        except Exception:
            pass
    return None


# ─────────────────────────────────────────────────────────── github signup

async def do_signup(tab, email: str, password: str, username: str,
                    dry_run: bool, max_user_tries: int = 6) -> dict:
    """
    Walk https://github.com/signup as far as possible.

    Returns a result dict with `success`, `username`, `stage`, `blocked`, `notes`.
    Never touches an existing account. On --dry-run it fills and screenshots but
    does not click the final account-creating buttons.
    """
    res: dict = {"success": False, "stage": "start", "blocked": None, "notes": []}

    await tab.get(GITHUB_SIGNUP)
    await asyncio.sleep(6)
    await screenshot(tab, "01_signup")

    # ── Step 1: email ──────────────────────────────────────────────
    print("      [gh] step 1/4: email", flush=True)
    if not await fill_first_input(tab, ['input#email', 'input[name=email]',
                                        'input[type=email]', 'input[autocomplete=email]'], email):
        res["stage"] = "email_input_not_found"
        res["notes"].append(f"url={await js(tab, 'location.href', '')}")
        await screenshot(tab, "01_email_missing")
        return res
    res["stage"] = "email_filled"
    await asyncio.sleep(0.5)

    cap = await detect_captcha(tab)
    if cap:
        res["blocked"] = f"captcha:{cap}"
        res["stage"] = "captcha_after_email"
        await screenshot(tab, "captcha_email")
        print(f"      [gh] ❌ blocked by {cap} — human required", flush=True)
        return res

    if dry_run:
        await screenshot(tab, "01_email_dryrun")
        res["stage"] = "dry_run_stopped_after_email"
        res["notes"].append("dry-run: stopped before clicking 'Create account'")
        return res

    await robust_click(tab, selector="button[type=submit]") or await _click_any(tab, ["Continue", "Create account", "Sign up"])
    await asyncio.sleep(5)
    await screenshot(tab, "02_after_email")

    cap = await detect_captcha(tab)
    if cap:
        res["blocked"] = f"captcha:{cap}"
        res["stage"] = "captcha_after_email_submit"
        await screenshot(tab, "captcha_after_email_submit")
        print(f"      [gh] ❌ blocked by {cap} — human required", flush=True)
        return res

    # ── Step 2: password ───────────────────────────────────────────
    print("      [gh] step 2/4: password", flush=True)
    if await js(tab, "!!document.querySelector('input[type=password]')", False):
        if not await fill_first_input(tab, ['input#password', 'input[name=password]',
                                            'input[type=password]'], password):
            res["stage"] = "password_input_not_found"
            return res
        res["stage"] = "password_filled"
        await asyncio.sleep(0.5)
    else:
        res["notes"].append("no password field seen (maybe email-only step)")

    if dry_run:
        await screenshot(tab, "02_password_dryrun")
        res["stage"] = "dry_run_stopped_after_password"
        return res

    await robust_click(tab, selector="button[type=submit]") or await _click_any(tab, ["Continue", "Next"])
    await asyncio.sleep(5)
    await screenshot(tab, "03_after_password")

    cap = await detect_captcha(tab)
    if cap:
        res["blocked"] = f"captcha:{cap}"
        res["stage"] = "captcha_after_password"
        await screenshot(tab, "captcha_after_password")
        print(f"      [gh] ❌ blocked by {cap} — human required", flush=True)
        return res

    # ── Step 3: username (availability retry) ──────────────────────
    print("      [gh] step 3/4: username", flush=True)
    if await js(tab, "!!document.querySelector('input#login,input[name=login]')", False):
        for attempt in range(max_user_tries):
            uname = username if attempt == 0 else gen_username()
            if not await fill_first_input(tab, ['input#login', 'input[name=login]'], uname):
                res["stage"] = "username_input_not_found"
                return res
            await asyncio.sleep(2)  # let the availability check fire
            # availability: look for an error hint
            err = await js(tab, """(()=>{const e=document.querySelector('[aria-live],.error,.flash-error,p.color-fg-danger');
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
        await screenshot(tab, "03_username_dryrun")
        res["stage"] = "dry_run_stopped_after_username"
        res["notes"].append("dry-run: stopped before creating the account")
        return res

    await robust_click(tab, selector="button[type=submit]") or await _click_any(tab, ["Continue", "Create account"])
    await asyncio.sleep(5)
    await screenshot(tab, "04_after_username")

    cap = await detect_captcha(tab)
    if cap:
        res["blocked"] = f"captcha:{cap}"
        res["stage"] = "captcha_before_create"
        await screenshot(tab, "captcha_before_create")
        print(f"      [gh] ❌ blocked by {cap} — human required", flush=True)
        return res

    # ── Step 4: email verification (8-digit launch code) ───────────
    print("      [gh] step 4/4: email launch code", flush=True)
    body = (await _page_text(tab) or "").lower()
    needs_code = "launch code" in body or "verify" in body or "email" in body
    if needs_code:
        # Ask GitHub to (re)send, best-effort.
        code_field = await js(tab, """(()=>{const cands=['input[name=code]','input#code',
            'input[autocomplete=one-time-code]','input[inputmode=numeric]','input[maxlength="8"]'];
            for(const s of cands){ if(document.querySelector(s)) return s; } return null;})()""", None)

        print("      [gh] waiting for email launch code from mailbox…", flush=True)
        code = await read_launch_code(email, timeout=240)
        if not code:
            res["stage"] = "launch_code_timeout"
            res["blocked"] = "email_launch_code_not_received"
            await screenshot(tab, "05_code_timeout")
            return res

        filled = False
        if code_field:
            filled = await cdp_type(tab, code_field, code)
        if not filled:
            filled = await fill_first_input(tab, ['input[name=code]', 'input#code',
                                                  'input[autocomplete=one-time-code]',
                                                  'input[inputmode=numeric]'], code)
        if not filled:
            res["blocked"] = "launch_code_field_not_found"
            res["stage"] = "code_enter_failed"
            res["notes"].append(f"code={code} (enter manually)")
            await screenshot(tab, "05_code_field_missing")
            return res
        res["notes"].append("launch_code_entered")
        await asyncio.sleep(1)
        await robust_click(tab, selector="button[type=submit]") or await _click_any(tab, ["Continue", "Verify"])
        await asyncio.sleep(6)
        await screenshot(tab, "06_after_code")

    # ── done? ──────────────────────────────────────────────────────
    url = await js(tab, "location.href", "")
    text = (await _page_text(tab) or "").lower()
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

async def do_education(tab, email: str, username: str, dry_run: bool) -> dict:
    """
    Open the Education application and fill what is safe. STOP before any
    attestation/photo step. Never submit an attestation.
    """
    res: dict = {"stage": "start", "filled": [], "needs_human": [], "url": None}

    await tab.get(GITHUB_EDU)
    await asyncio.sleep(7)
    await screenshot(tab, "10_education")
    res["url"] = await js(tab, "location.href", "")

    body = (await _page_text(tab) or "")
    low = body.lower()
    if "sign in" in low and "application" not in low:
        res["stage"] = "needs_login"
        res["needs_human"].append("Sign in to the new GitHub account, then re-open the Education form.")
        return res

    # School name / email fields vary; fill best-effort.
    print("      [edu] filling application fields (best-effort)", flush=True)

    # Email field (academic email)
    if await fill_first_input(tab, ['input[type=email]', 'input[name*=email]',
                                    'input[id*=email]'], email):
        res["filled"].append("email")

    # School name / institution search
    if await fill_first_input(tab, ['input[name*=school]', 'input[id*=school]',
                                    'input[placeholder*=school]', 'input[placeholder*=School]'], SCHOOL_NAME):
        res["filled"].append(f"school={SCHOOL_NAME}")
        await asyncio.sleep(2)
        # try to pick an autocomplete suggestion containing BINUS
        picked = await js(tab, """(()=>{const els=[...document.querySelectorAll('li,[role=option]')];
            const t=els.find(e=>/binus/i.test(e.innerText||'')); if(t){t.click(); return (t.innerText||'').trim();}
            return '';})()""", "")
        if picked:
            res["filled"].append(f"school_pick={picked[:60]}")

    if dry_run:
        res["stage"] = "dry_run_stopped"
        res["needs_human"].append("Review filled fields, then complete manually.")
        await screenshot(tab, "11_education_dryrun")
        return res

    # Detect the attestation / photo requirement and STOP there.
    low = (await _page_text(tab) or "").lower()
    if any(k in low for k in ("i attest", "attest", "upload", "student id",
                              "proof of enrollment", "enrollment", "school-issued",
                              "photo of", "documentation")):
        res["stage"] = "stopped_before_attestation"
        res["needs_human"].append(
            "Attestation / photo of school ID is required here. This is a human step "
            "(legal attestation + document upload + GitHub's manual review).")
        await screenshot(tab, "12_attestation_stop")
        return res

    res["stage"] = "form_filled_review"
    res["needs_human"].append("Verify fields and click Submit yourself; review is manual.")
    await screenshot(tab, "13_education_ready")
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
    print("  github_farm --check")
    print("=" * 60)
    ok = True

    try:
        import nodriver as uc  # noqa
        print(f"  ✅ nodriver          ({uc.__file__})")
    except Exception as e:
        ok = False
        print(f"  ❌ nodriver          {e}")

    reader = SCRIPTS / "school_mail_browser.py"
    print(f"  {'✅' if reader.exists() else '❌'} school mailbox reader  ({reader})")
    if not reader.exists():
        ok = False

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

    print("\n  NOTE: captcha/Arkose and the Education photo+attestation step are NOT automatable.")
    print("=" * 60)
    return 0 if ok else 1


# ─────────────────────────────────────────────────────────── main flow

def build_email(index: int) -> str:
    """Plus-addressed school mailbox: raymondi+gh<N>@binus.ac.id."""
    if index <= 0:
        return f"{BASE_LOCAL}@{SCHOOL_DOMAIN}"    # plain mailbox (careful!)
    return f"{BASE_LOCAL}+gh{index}@{SCHOOL_DOMAIN}"


async def run(index: int, headless: bool, proxy: str | None, dry_run: bool) -> int:
    import nodriver as uc

    email = build_email(index)
    username = gen_username()
    password = gen_password()

    print("=" * 60, flush=True)
    print("  GITHUB FARM (nodriver / CDP)", flush=True)
    print("=" * 60, flush=True)
    print(f"  index     : {index}", flush=True)
    print(f"  email     : {email}", flush=True)
    print(f"  username  : {username}", flush=True)
    print(f"  password  : {password}", flush=True)
    print(f"  dry-run   : {dry_run}", flush=True)
    print(f"  headless  : {headless}", flush=True)
    print(f"  proxy     : {proxy or '(none)'}", flush=True)
    print("-" * 60, flush=True)

    args = [f"--proxy-server={proxy}"] if proxy else None
    browser = await uc.start(headless=headless, sandbox=False, lang="en-US", browser_args=args)
    record = {
        "email": email,
        "username": username,
        "password": password,
        "index": index,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "note": "School mailbox is the ONLY address used (plus-addressing). Does not touch 'Ini-Amin'.",
    }
    try:
        tab = await browser.get(GITHUB_SIGNUP)
        await asyncio.sleep(6)

        signup = await do_signup(tab, email, password, username, dry_run)
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
        if signup.get("success") or dry_run:
            edu = await do_education(tab, email, record["username"], dry_run)
        record["education"] = edu

        print("\n  --- education result ---", flush=True)
        print(f"      stage   : {edu.get('stage')}", flush=True)
        print(f"      filled  : {', '.join(edu.get('filled', [])) or '(none)'}", flush=True)
        for h in edu.get("needs_human", []):
            print(f"      human → : {h}", flush=True)

        save_account(record)
        return 0 if signup.get("success") or dry_run else 1
    finally:
        if not headless:
            print("\n  browser left open 8s for inspection…", flush=True)
            await asyncio.sleep(8)
        try:
            browser.stop()
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(
        description="GitHub signup + Education application helper (nodriver). "
                    "Automates form fill + email launch-code; captcha and identity "
                    "attestation remain human steps.")
    ap.add_argument("--check", action="store_true", help="check deps + mailbox config, then exit")
    ap.add_argument("--index", type=int, default=1, help="N for raymondi+gh<N>@binus.ac.id")
    ap.add_argument("--headless", action="store_true", help="run browser headless")
    ap.add_argument("--proxy", default=None, help="proxy URL, e.g. http://user:pass@host:port")
    ap.add_argument("--dry-run", action="store_true",
                    help="walk the signup flow, screenshot, but do not submit/create")
    args = ap.parse_args()

    if args.check:
        return run_check()
    return asyncio.run(run(args.index, args.headless, args.proxy, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
