#!/usr/bin/env python3
"""
ChatGPT K-12 flow on nodriver — reliable CDP-native clicks.

Why nodriver instead of DrissionPage: ChatGPT's "Verify status" is a React
button that ignores synthetic clicks (element.click() / dispatchEvent).
DrissionPage only offers synthetic clicks; nodriver can fire real CDP
Input.dispatchMouseEvent (mouseMoved -> mousePressed -> mouseReleased), which
the browser treats as genuine user input. This is the same engine that beat
Cloudflare Turnstile in the CF signup flow.

Mail: uses KancaHub's own relay (Supabase + Cloudflare Email Routing), not
temp.tf. Sessions are captured from /api/auth/session and saved for 9Router.

Run from anywhere; it bootstraps the K-12 tool dir onto sys.path for K12Verifier.
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

import nodriver as uc
import requests

HOME = Path.home()
AUTO_FREECF = HOME / "Auto-FreeCF"
K12_DIR = HOME / "petani-proxy" / "Farm-Acc-ChatGPT-K-12-Teachers" / "PyRuntime_64"
SESSIONS = K12_DIR / "k12_sessions.json"
CREATED = K12_DIR / "created_k12_accounts.txt"

CHATGPT = "https://chatgpt.com"


# ───────────────────────────────────────────── config / mail relay

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


def create_mailbox() -> dict:
    dom = random.choice([d.strip() for d in DOMAINS if d.strip()])
    r = requests.post(f"{MAIL_BASE}/new_address", json={"domain": dom},
                      headers={"x-api-key": MAIL_KEY}, timeout=60)
    r.raise_for_status()
    return r.json()  # {address, jwt, domain}


def poll_mail(jwt: str) -> list[dict]:
    r = requests.get(f"{MAIL_BASE}/parsed_mails",
                     headers={"Authorization": f"Bearer {jwt}", "x-api-key": MAIL_KEY}, timeout=30)
    r.raise_for_status()
    d = r.json()
    return d if isinstance(d, list) else d.get("results", [])


def _blob(m: dict) -> str:
    return " ".join(str(m.get(k, "")) for k in ("subject", "text", "body", "html", "snippet", "from"))


async def wait_for_otp(jwt: str, timeout: int = 180) -> str | None:
    print(f"      [mail] polling relay for OTP (max {timeout}s)…", flush=True)
    start = time.time()
    seen = set()
    while time.time() - start < timeout:
        try:
            for m in poll_mail(jwt):
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


# ───────────────────────────────────────────── nodriver helpers

def _unwrap(v):
    if isinstance(v, dict) and "type" in v and "value" in v:
        return v["value"]
    if isinstance(v, list):
        return [_unwrap(x) for x in v]
    return v


async def robust_click(tab, selector: str | None = None, text: str | None = None,
                       timeout: float = 5.0) -> bool:
    """Real CDP mouse click on a centered element (CSS selector, else text match).

    Used for buttons whose label is unique on the page (e.g. "Verify status").
    For the login 'Continue' button use click_continue() — a text match there is
    ambiguous ('Continue with Google' also matches).
    """
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


async def click_continue(tab, timeout: float = 5.0) -> bool:
    """Click the EXACT 'Continue' button (never 'Continue with Google/Apple/phone').

    The ChatGPT login page has four buttons; a loose /continue/i regex matches
    'Continue with Google' first and derails the flow into Google OAuth. This
    matches the label exactly, preferring a submit button.
    """
    for sel in ("button[type=submit]", "button"):
        for el in await _query_all(tab, sel):
            txt = (el.get("text") or "").strip()
            if txt in ("Continue", "Next", "Sign up", "Verify", "Submit", "Log in"):
                if await _cdp_click_el(tab, el):
                    return True
    return bool(await js(tab, """
        (function(){
          const ok=['Continue','Next','Sign up','Verify','Submit','Log in'];
          const b=[...document.querySelectorAll('button')].find(x=>ok.includes(x.innerText.trim()));
          if(b){ b.click(); return true; } return false;
        })()
    """, False))


async def find_sheerid_anchor(tab) -> str:
    """Return the SheerID URL from the DOM, if present.

    Per research: the K-12 page delivers the SheerID URL as a plain
    <a href="https://services.sheerid.com/verify/..."> anchor. Reading the href
    is far more reliable than clicking the button. We also scan the whole HTML
    and any iframes for a sheerid.com/verify URL.
    """
    # 1) anchor href
    href = await js(tab, """(()=>{for(const a of document.querySelectorAll('a[href]')){
        const h=a.href||''; if(h.includes('sheerid.com/verify')) return h;} return '';})()""", "")
    if href:
        return href
    # 2) any element with the URL in an attribute
    href = await js(tab, """(()=>{const els=document.querySelectorAll('[href],[data-href],[data-url]');
        for(const e of els){ for(const a of ['href','data-href','data-url']){
        const v=e.getAttribute(a)||''; if(v.includes('sheerid.com/verify')) return v; }} return '';})()""", "")
    if href:
        return href
    # 3) scan full HTML (covers React-embedded URLs / __NEXT_DATA__)
    html = await js(tab, "document.documentElement ? document.documentElement.outerHTML : ''", "")
    if html:
        m = re.search(r'https://services\.sheerid\.com/verify/[^\s"\'<>\\]+', html)
        if m:
            return m.group(0)
    # 4) scan JS globals
    href = await js(tab, """(()=>{try{const s=JSON.stringify(window.__NEXT_DATA__||{});
        const m=s.match(/https:\\/\\/services\\.sheerid\\.com\\/verify\\/[^"\\\\]+/);return m?m[0]:'';}catch(e){return '';}})()""", "")
    if href:
        return href.replace("\\/", "/")
    # 5) iframes
    for f in tab.frames if hasattr(tab, "frames") else []:
        try:
            u = await js(f, "location.href", "")
            if "sheerid.com/verify" in u:
                return u
        except Exception:
            pass
    return ""


async def click_verify_status(tab, wait_ready: float = 60.0) -> bool:
    """Click the K-12 verification button via a real CDP mouse click.

    The button first renders as "Checking eligibility..." (disabled) while
    OpenAI calls the eligibility API, then becomes "Verify status". We must wait
    for that transition before clicking — clicking during the check does nothing.
    """
    deadline = time.time() + wait_ready
    while time.time() < deadline:
        btns = await _query_all(tab, "button")
        for el in btns:
            t = (el.get("text") or "").strip()
            if t == "Verify status":
                return await _cdp_click_el(tab, el)
            if t in ("Checking eligibility...", "Checking eligibility"):
                # still waiting on the API; back off and retry
                break
        await asyncio.sleep(1.5)
    return False


async def _query_all(tab, selector: str) -> list[dict]:
    """Return [{idx, text, x, y, w, h}] for all elements matching selector."""
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


async def _cdp_click_el(tab, el: dict) -> bool:
    return await _cdp_click_xy(tab, el["x"], el["y"])


async def js(tab, expr: str, default=None):
    """Evaluate JS and return a plain Python value.

    nodriver returns a RemoteObject when the JS value is falsy (False/0/""/None)
    even with return_by_value=True, so unwrap those too.
    """
    try:
        r = await tab.evaluate(expr, return_by_value=True)
    except Exception:
        return default
    # unwrap RemoteObject
    if r is not None and r.__class__.__name__ == "RemoteObject":
        v = getattr(r, "value", None)
        if v is None:
            dv = getattr(r, "deep_serialized_value", None)
            v = getattr(dv, "value", None) if dv else None
        r = v
    r = _unwrap(r)
    return default if r is None else r


async def react_fill(tab, selector: str, value: str) -> bool:
    """Fill a React-controlled input using the native value setter + InputEvent."""
    expr = """
(function(){
  const s = %s, v = %s;
  const el = document.querySelector(s);
  if (!el) return false;
  el.focus(); el.click();
  const proto = (el instanceof HTMLTextAreaElement) ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
  const tr = el._valueTracker; if (tr) tr.setValue('');
  if (setter) setter.call(el, v); else el.value = v;
  el.dispatchEvent(new InputEvent('beforeinput',{bubbles:true,data:v,inputType:'insertText'}));
  el.dispatchEvent(new InputEvent('input',{bubbles:true,data:v,inputType:'insertText'}));
  el.dispatchEvent(new Event('change',{bubbles:true}));
  return String(el.value||'').trim() === String(v||'').trim();
})()
""" % (json.dumps(selector), json.dumps(value))
    return bool(await js(tab, expr, False))


async def cdp_type(tab, selector: str, value: str) -> bool:
    """Fill an input the way DrissionPage does (proven on this very page):

      1. real click to focus
      2. clear = Ctrl+A then Delete (REAL key events, not JS)
      3. type  = CDP Input.insertText  (not per-char dispatch)

    This is what reliably replaces values in OpenAI's React inputs; a plain JS
    value setter leaves React's state intact and the text appends (e.g. 33->3333).
    """
    # 1. focus
    await robust_click(tab, selector=selector, timeout=4)
    await asyncio.sleep(0.2)

    # 2. clear via Ctrl+A + Delete (real key events)
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

    # 3. type via Input.insertText (single call — replaces React value cleanly)
    try:
        await tab.send(uc.cdp.input_.insert_text(str(value)))
    except Exception:
        for ch in str(value):
            try:
                await tab.send(uc.cdp.input_.insert_text(ch))
            except Exception:
                pass
    await asyncio.sleep(0.35)

    got = await js(tab, f"(()=>{{const e=document.querySelector({json.dumps(selector)});return e?e.value:null;}})()", "")
    ok = str(got).strip() == str(value).strip()
    print(f"      [type] {selector} <- {value!r} => {got!r} ok={ok}", flush=True)
    return ok


async def fill_first_input(tab, selectors: list[str], value: str) -> bool:
    for sel in selectors:
        found = await js(tab, f"!!document.querySelector({json.dumps(sel)})", False)
        if found and await react_fill(tab, sel, value):
            return True
    # fallback: any visible text input
    sel = await js(tab, """(()=>{const els=[...document.querySelectorAll('input')].filter(e=>{
        const t=(e.type||'text').toLowerCase(); const r=e.getBoundingClientRect();
        return !['hidden','submit','checkbox','radio','file','search'].includes(t) && r.width>0 && r.height>0;
    }); return els.length ? (els[0].id ? '#'+els[0].id : null) : null;})()""", None)
    if sel:
        return await react_fill(tab, sel, value)
    return False


# ───────────────────────────────────────────── about-you handling

def main_tab(browser):
    """The live page tab. nodriver's `targets` can report a blank URL after a
    cross-origin navigation, but `browser.main_tab` always tracks reality."""
    try:
        mt = getattr(browser, "main_tab", None)
        if mt is not None:
            return mt
    except Exception:
        pass
    return None


async def find_live_tab(browser, *substrs):
    """Return the page tab whose LIVE url contains any substring.

    `evaluate('location.href')` works even when `targets[].url` is blank, so we
    probe every page target directly. Falls back to main_tab.
    """
    for t in list(browser.targets):
        try:
            if getattr(t, "type_", "") != "page":
                continue
        except Exception:
            continue
        u = await js(t, "location.href", "")
        if not u:
            continue
        for s in substrs:
            if s in u:
                return t
    return main_tab(browser)


async def live_url(tab) -> str:
    return await js(tab, "location.href", "")


def _dump_tabs(browser) -> str:
    parts = []
    try:
        for t in browser.targets:
            parts.append(f"{getattr(t,'type_','?')}:{(getattr(t,'url','') or '')[:50]}")
    except Exception:
        pass
    return " | ".join(parts)


async def fill_about_you(tab) -> bool:
    """Handle the age/name gate (auth.openai.com/about-you).

    OpenAI uses TWO shapes for this gate; detect fields, don't assume:
      A) 'How old are you?'  -> Full name (text) + Age (number)
      B) 'Lets confirm your age' -> Full name (text) + Birthday (date/mm-dd-yyyy)
    """
    has_name = await js(tab, "!!document.querySelector('input[name=name]')", False)
    if not has_name:
        return False

    name = f"{random.choice(['James','Robert','John','Michael','David'])} " \
           f"{random.choice(['Miller','Smith','Johnson','Williams','Brown'])}"
    yr = random.randint(1975, 1995)
    age = 2026 - yr
    dob = f"{random.randint(1,12):02d}/{random.randint(1,28):02d}/{yr}"

    # --- Full name (plain text) ---
    await react_fill(tab, 'input[name=name]', name)
    await asyncio.sleep(0.5)
    name_val = await js(tab, "(()=>{const e=document.querySelector('input[name=name]');return e?e.value:'';})()", "")
    print(f"      [about-you] name field => {name_val!r}", flush=True)

    # --- Age field? (shape A) ---
    age_sel = None
    for sel in ('input[name=age]', 'input[id*=age i]', 'input[type=number]'):
        if await js(tab, f"!!document.querySelector({json.dumps(sel)})", False):
            age_sel = sel
            break

    filled = False
    if age_sel:
        # real per-char typing into the number field
        filled = await cdp_type(tab, age_sel, str(age))
        av = await js(tab, f"(()=>{{const e=document.querySelector({json.dumps(age_sel)});return e?e.value:'';}})()", "")
        print(f"      [about-you] age field => {av!r} (want {age})", flush=True)
    else:
        # --- Birthday (shape B) ---
        bsel = None
        for sel in ('input[name=birthday]', 'input[name=birthDate]', 'input[id*=birth i]',
                    'input[placeholder*=dd i]', 'input[placeholder*=MM i]', 'input[type=date]'):
            if await js(tab, f"!!document.querySelector({json.dumps(sel)})", False):
                bsel = sel
                break
        if bsel is None:
            bsel = await js(tab, """(()=>{const els=[...document.querySelectorAll('input')].filter(e=>{
                const t=(e.type||'').toLowerCase(); const r=e.getBoundingClientRect();
                return !['hidden','submit','checkbox','radio','file'].includes(t) && r.width>0;});
                const b=els[1]; if(!b) return null;
                return b.name ? ('input[name="'+b.name+'"]') : (b.id ? ('#'+b.id) : null);})()""", None)
        if bsel:
            is_date = await js(tab, f"(()=>{{const e=document.querySelector({json.dumps(bsel)});return e?(e.type==='date'):false;}})()", False)
            if is_date:
                iso = f"{yr:04d}-{random.randint(1,12):02d}-{random.randint(1,28):02d}"
                await js(tab, f"""(()=>{{const e=document.querySelector({json.dumps(bsel)}); if(!e) return '';
                    e.focus();
                    const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
                    s.call(e, {json.dumps(iso)});
                    e.dispatchEvent(new InputEvent('input',{{bubbles:true,data:{json.dumps(iso)},inputType:'insertText'}}));
                    e.dispatchEvent(new Event('change',{{bubbles:true}})); return e.value;}})()""", "")
                filled = True
                print(f"      [about-you] birthday(date) => {iso}", flush=True)
            else:
                filled = await cdp_type(tab, bsel, dob)

    print(f"      [about-you] name={name!r} age={age} dob={dob!r} filled={filled}", flush=True)
    await asyncio.sleep(0.8)
    ok = await click_continue(tab)
    if not ok:
        btns = await js(tab, """JSON.stringify([...document.querySelectorAll('button')].map(b=>b.innerText.trim()).filter(Boolean))""", "[]")
        print(f"      [about-you] buttons={btns}", flush=True)
        ok = bool(await js(tab, """(()=>{const b=[...document.querySelectorAll('button')]
            .filter(x=>!x.disabled).pop(); if(b){b.click(); return true;} return false;})()""", False))
    print(f"      [about-you] Continue clicked={ok}", flush=True)

    # verify we left the page (validation passed)
    for _ in range(10):
        await asyncio.sleep(1)
        u = await js(tab, "location.href", "")
        if "about-you" not in u:
            print(f"      [about-you] left page -> {u[:60]}", flush=True)
            break
        err = await js(tab, """(()=>{const e=document.querySelector('[role=alert],.text-red-500,[class*=error]');
            return e?e.innerText.slice(0,80):'';})()""", "")
        if err:
            print(f"      [about-you] validation error: {err!r}", flush=True)
    return True


# ───────────────────────────────────────────── session capture

async def capture_session(browser, tab=None) -> dict:
    """Read the ChatGPT OAuth session IN-PLACE on the live tab.

    Do NOT navigate to /api/auth/session in a new tab: the ChatGPT session is
    per-tab context, so a fresh tab shows "Your session has ended". Instead,
    fetch() the endpoint from the page that actually signed in.
    """
    if tab is None:
        tab = await find_live_tab(browser, "chatgpt.com", "openai.com", "auth.openai.com")
    if tab is None:
        return {"error": "no_live_tab"}
    try:
        raw = await tab.evaluate(
            """
            (async () => {
              try {
                const r = await fetch('/api/auth/session', { credentials: 'include' });
                return await r.text();
              } catch (e) { return JSON.stringify({ error: String(e) }); }
            })()
            """,
            await_promise=True, return_by_value=True,
        )
        if raw is not None and raw.__class__.__name__ == "RemoteObject":
            raw = getattr(raw, "value", None)
        if isinstance(raw, dict) and "value" in raw:
            raw = raw["value"]
        sess = json.loads(raw) if isinstance(raw, str) and raw.strip().startswith("{") else {}
    except Exception as e:  # noqa: BLE001
        sess = {"error": str(e)}

    # refresh token from cookies
    try:
        for c in await browser.cookies.get_all():
            nm = getattr(c, "name", None) or (c.get("name") if isinstance(c, dict) else None)
            vl = getattr(c, "value", None) or (c.get("value") if isinstance(c, dict) else None)
            if nm in ("__Secure-next-auth.session-token", "next-auth.session-token",
                      "__Secure-authjs.session-token"):
                sess["refreshToken"] = vl
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
    SESSIONS.write_text(json.dumps(data, indent=2))
    print(f"[session] saved -> {SESSIONS} (plan={sess.get('planType')})", flush=True)


# ───────────────────────────────────────────── main

def gen_password() -> str:
    return "TeacherK12!" + "".join(random.choices(string.ascii_letters + string.digits, k=10)) + "#2026"


async def run_flow(headless: bool = False, proxy: str | None = None) -> dict:
    print("=" * 60, flush=True)
    print("  CHATGPT K-12 (nodriver / CDP)", flush=True)
    print("=" * 60, flush=True)

    if not SUPABASE_URL or not MAIL_KEY:
        return {"success": False, "error": "mail_relay_unconfigured"}

    print("[1/6] Creating mailbox on relay…", flush=True)
    mail = create_mailbox()
    email, jwt = mail["address"], mail["jwt"]
    print(f"      [+] {email}", flush=True)
    password = gen_password()
    print(f"[2/6] Password: {password}", flush=True)

    print("[3/6] Launching nodriver browser…", flush=True)
    args = [f"--proxy-server={proxy}"] if proxy else None
    browser = await uc.start(headless=headless, sandbox=False, lang="en-US", browser_args=args)
    result = {"success": False, "email": email, "password": password}
    try:
        tab = await browser.get(f"{CHATGPT}/auth/login/?next=%2Fk12-verification")
        await asyncio.sleep(6)

        # email
        if not await fill_first_input(tab, ['input[type=email]', 'input[name=email]',
                                            'input[autocomplete=email]'], email):
            result["error"] = "email_input_not_found"
            return result
        await asyncio.sleep(1)
        await click_continue(tab)
        await asyncio.sleep(4)

        # password (account creation). The create-account page has TWO password
        # fields (password + confirm). Fill all password inputs that are empty.
        if await js(tab, "!!document.querySelector('input[type=password]')", False):
            n_pw = await js(tab, "document.querySelectorAll('input[type=password]').length", 1)
            for idx in range(int(n_pw) if n_pw else 1):
                sel = f"input[type=password]:nth-of-type({idx+1})" if n_pw > 1 else "input[type=password]"
                # prefer index-based selection for reliability
                sels = [f"input[type=password][name=password]", "input[type=password]"]
                try:
                    await cdp_type(tab, sels[0], password) if idx == 0 else await cdp_type(tab, sels[1], password)
                except Exception:
                    await cdp_type(tab, "input[type=password]", password)
            # fallback: ensure the field(s) hold the password
            await js(tab, f"""(()=>{{const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
                const ps=[...document.querySelectorAll('input[type=password]')];
                ps.forEach(e=>{{ if(!e.value){{ e.focus(); e.click(); s.call(e,{json.dumps(password)});
                    e.dispatchEvent(new InputEvent('input',{{bubbles:true,data:{json.dumps(password)},inputType:'insertText'}}));
                    e.dispatchEvent(new Event('change',{{bubbles:true}})); }} }});
                return ps.map(e=>e.value.length);}})()""", "[]")
            await asyncio.sleep(1)
            await click_continue(tab)
            await asyncio.sleep(4)

        # OTP — find the field, fill it with cdp_type (React-safe: focus +
        # Ctrl+A/Delete + Input.insertText), VERIFY it holds the code, and only
        # then click Continue. Never submit an empty code.
        print("[4/6] Waiting for OTP from relay…", flush=True)
        otp = await wait_for_otp(jwt, timeout=180)
        if otp:
            # wait for an OTP input to exist
            otp_sel = None
            for _ in range(25):
                otp_sel = await js(tab, """(()=>{
                    const cands=['input[autocomplete=one-time-code]','input[name=code]',
                      'input[name=otp]','input[inputmode=numeric]','input[type=number]','input[type=tel]'];
                    for(const s of cands){ const e=document.querySelector(s);
                      if(e && e.getBoundingClientRect().width>0) return s; }
                    const els=[...document.querySelectorAll('input')].filter(e=>{
                      const t=(e.type||'').toLowerCase(); const r=e.getBoundingClientRect();
                      return r.width>0 && ['text','number','tel'].includes(t); });
                    return els[0] ? null : null;})()""", None)
                if otp_sel:
                    break
                await asyncio.sleep(1)
            if not otp_sel:
                otp_sel = "input[type=text],input[type=number],input[type=tel]"

            typed = False
            for attempt in range(4):
                typed = await cdp_type(tab, otp_sel, otp)
                val = await js(tab, f"(()=>{{const e=document.querySelector({json.dumps(otp_sel)});return e?e.value:'';}})()", "")
                if str(val).strip() == str(otp).strip():
                    typed = True
                    break
                print(f"      [otp] attempt {attempt+1}: field={val!r}, retrying…", flush=True)
                await asyncio.sleep(1)

            if not typed:
                print("      ✗ OTP field could not be filled — NOT submitting (avoids the empty-code error)", flush=True)
            else:
                print(f"      [+] OTP typed ok ({otp}); submitting…", flush=True)
                await asyncio.sleep(0.5)
                await click_continue(tab)

            # wait until we leave the OTP screen (code consumed)
            consumed = False
            for _ in range(40):
                await asyncio.sleep(2)
                still = await js(tab, """(()=>{const e=[...document.querySelectorAll('input')]
                    .find(x=>x.autocomplete==='one-time-code'||['code','otp'].includes(x.name)
                    || (x.getAttribute('aria-label')||'').toLowerCase().includes('code'));
                    return !!e;})()""", False)
                url = await js(tab, "location.href", "")
                if not still or "email-verification" not in url:
                    consumed = True
                    break
                # retry submit in case it didn't register
                if _ % 6 == 5:
                    await click_continue(tab)
            _u = await js(tab, "location.href", "")
            print(f"      [otp] consumed={consumed} url={_u[:60]}", flush=True)
        else:
            print("      [!] OTP not found", flush=True)
        await asyncio.sleep(6)
        print(f"      [tabs after OTP] {_dump_tabs(browser)}", flush=True)

        # Re-acquire the live tab by URL (the signup may have moved to a new tab).
        # NOTE: do NOT call browser.get() here — that would start a fresh tab and
        # drop the authenticated context.
        lt = await find_live_tab(browser, "about-you", "auth.openai.com", "chatgpt.com")
        if lt is not None:
            tab = lt
        _au = await js(tab, "location.href", "")
        if not _au:
            # try every page target directly
            for cand in list(browser.targets):
                if getattr(cand, "type_", "") == "page":
                    u = await js(cand, "location.href", "")
                    if u:
                        tab = cand
                        _au = u
                        break
        print(f"      [about-you] live tab url={_au[:70]}", flush=True)

        # capture session IN-PLACE (no navigation — that caused 'session ended')
        for _ in range(8):
            s = await capture_session(browser, tab)
            if s.get("accessToken"):
                save_session(s, email)
                break
            await asyncio.sleep(3)
        else:
            print("      [!] session not captured yet (will retry after verify)", flush=True)

        # about-you — the live tab.
        for i in range(40):
            if await js(tab, "!!document.querySelector('input[name=name]')", False):
                _u = await js(tab, "location.href", "")
                print(f"      [about-you] form found (loop {i}) url={_u[:60]}", flush=True)
                await fill_about_you(tab)
                break
            _u = await js(tab, "location.href", "")
            if "k12-verification" in _u or "chatgpt.com" in _u:
                print(f"      [k12] page reached (loop {i})", flush=True)
                break
            if i in (8, 20):
                _u = await js(tab, "location.href", "")
                print(f"      [wait i={i}] url={_u[:70]}", flush=True)
            await asyncio.sleep(1)

        # ---- click "Verify status" (waits out the 'Checking eligibility...' phase) ----
        print("[5/6] Driving K-12 verification page…", flush=True)
        sheerid = None

        def _find_sheerid_in(url: str) -> str:
            return url if "sheerid.com/verify" in url else ""

        for round_ in range(8):
            _u = await js(tab, "location.href", "")
            if _find_sheerid_in(_u):
                sheerid = _u
                break
            # PRIMARY: read the SheerID anchor/href straight from the DOM
            href = await find_sheerid_anchor(tab)
            if _find_sheerid_in(href):
                sheerid = href
                print(f"      [+] SheerID from DOM: {sheerid[:80]}", flush=True)
                break
            # wait for the button to become clickable, then click
            clicked = await click_verify_status(tab, wait_ready=60)
            if clicked:
                print(f"      [click] Verify status #{round_+1}", flush=True)
            # give the click time to produce a redirect / new tab / anchor
            for _ in range(10):
                await asyncio.sleep(1.5)
                href = await find_sheerid_anchor(tab)
                if _find_sheerid_in(href):
                    sheerid = href
                    break
                for t in list(browser.targets):
                    if getattr(t, "type_", "") != "page":
                        continue
                    u = await js(t, "location.href", "")
                    if _find_sheerid_in(u):
                        sheerid = u
                        break
                if sheerid:
                    break
            if sheerid:
                break
            _u = await js(tab, "location.href", "")
            print(f"      [verify round {round_+1}] url={_u[:70]}", flush=True)

        if sheerid:
            print(f"      [+] SheerID URL: {sheerid[:90]}", flush=True)
            result["sheerid_url"] = sheerid
            try:
                sys.path.insert(0, str(K12_DIR))
                from script import K12Verifier  # type: ignore
                res = K12Verifier(sheerid, use_temp_email=True, manual_email=email).verify()
                result["verify"] = res
                result["success"] = bool(res.get("success"))
                print(f"      K12Verifier: success={result['success']}", flush=True)
            except Exception as e:  # noqa: BLE001
                result["verify_error"] = str(e)
                print(f"      K12Verifier error: {e}", flush=True)
            s2 = await capture_session(browser, tab)
            if s2.get("accessToken"):
                save_session(s2, email)
        else:
            _u = await js(tab, "location.href", "")
            result["error"] = f"no_sheerid_url (url={_u[:80]})"

        CREATED.parent.mkdir(parents=True, exist_ok=True)
        with CREATED.open("a", encoding="utf-8") as f:
            f.write(f"{email}----{password}----{datetime_now()}\n")
        print(f"\n[+] creds -> {CREATED}", flush=True)
        return result
    finally:
        try:
            browser.stop()
        except Exception:
            pass


def datetime_now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def main() -> int:
    ap = argparse.ArgumentParser(description="ChatGPT K-12 flow on nodriver (CDP clicks)")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--proxy", default=None)
    a = ap.parse_args()
    r = asyncio.run(run_flow(headless=a.headless, proxy=a.proxy))
    print(json.dumps(r, indent=2, default=str))
    return 0 if r.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
