#!/usr/bin/env python3
"""
School-mailbox OTP reader via BROWSER (nodriver).

BINUS (and most M365 tenants) block IMAP basic auth, and creating an OAuth2 app
needs tenant-admin rights we don't have. So we log into Outlook Web with the
real credentials and read the inbox from the DOM — the same way a human would.

Config (~/.config/auto-freecf/.env):
    SCHOOL_EMAIL=you@binus.ac.id
    SCHOOL_MAIL_PASSWORD=...
    SCHOOL_MAIL_URL=https://outlook.office.com/mail/   # optional override

Usage:
    python3 school_mail_browser.py login          # open + log in, keep session for inspection
    python3 school_mail_browser.py selftest        # log in and print inbox subjects
    python3 school_mail_browser.py otp --timeout 180

Design notes:
- Uses a persistent user-data-dir (~/.config/auto-freecf/school-profile) so the
  Microsoft session/cookies survive between runs (avoids re-login + MFA prompts).
- Microsoft login has several screens (email -> password -> "Stay signed in?").
  We handle each with waits and best-effort clicks.
- If the tenant enforces MFA (authenticator app), a fully-unattended login is not
  possible; run `selftest` once manually to complete MFA, then the persisted
  profile lets `otp` reuse it until the session expires.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
from pathlib import Path

import nodriver as uc

PROFILE_DIR = Path.home() / ".config" / "auto-freecf" / "school-profile"
MAIL_URL = "https://outlook.office.com/mail/"


def _load_env() -> dict:
    env = {}
    p = Path.home() / ".config" / "auto-freecf" / ".env"
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


_ENV = _load_env()


def cfg(key: str, default: str = "") -> str:
    return os.environ.get(key) or _ENV.get(key, default)


async def _js(tab, expr, default=None):
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


async def _fill(tab, selector: str, value: str) -> bool:
    """Fill an MS login input with REAL keystrokes (nodriver send_keys).

    MS's login SPA ignores a bare value= / InputEvent (the box looks empty and
    the form submits blank), which is why the password step never logged in.
    Prefer element.send_keys (trusted key events); fall back to the native
    value setter only if send_keys is unavailable.
    """
    el = None
    if hasattr(tab, "select"):
        for _ in range(20):                    # wait for the SPA to render the field
            try:
                el = await tab.select(selector, timeout=2)
            except Exception:
                el = None
            if el is not None:
                break
            await asyncio.sleep(0.75)
    if el is not None:
        try:
            await el.clear_input()
        except Exception:
            pass
        try:
            await el.click()
        except Exception:
            pass
        try:
            await el.send_keys(value)          # real key events
            return True
        except Exception:
            pass
    # fallback: native setter + input event
    await _js(tab, """(()=>{const e=document.querySelector(%s);
        if(!e) return 0; e.focus();
        const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
        s.call(e,%s); e.dispatchEvent(new InputEvent('input',{bubbles:true,data:%s}));
        return 1;})()""" % (repr(selector).replace("'", '"'), repr(value).replace("'", '"'),
                            repr(value).replace("'", '"')), 0)
    return True


async def _field_value(tab, selector: str) -> str:
    return await _js(tab, "(document.querySelector(%s)||{}).value||''"
                     % repr(selector).replace("'", '"'), "")


async def do_login(tab, email: str, password: str) -> None:
    """Walk the Microsoft login flow (real keystrokes so the fields actually fill)."""
    print("      [school] completing Microsoft login…", flush=True)
    email_sel = "input[type=email],input[name=loginfmt]"
    pw_sel = "input[type=password],input[name=passwd]"

    # email step
    for _ in range(20):
        if await _js(tab, "!!document.querySelector(%s)" % repr(email_sel).replace("'", '"'), False):
            break
        await asyncio.sleep(1)
    await _fill(tab, email_sel, email)
    await asyncio.sleep(0.6)
    print(f"      [school] email field now: {bool(await _field_value(tab, email_sel))}", flush=True)
    await _maybe_click(tab, "Next")
    await asyncio.sleep(4)

    # password step
    for _ in range(20):
        if await _js(tab, "!!document.querySelector(%s)" % repr(pw_sel).replace("'", '"'), False):
            break
        await asyncio.sleep(1)
    if not await _js(tab, "!!document.querySelector(%s)" % repr(pw_sel).replace("'", '"'), False):
        print("      [school] password field never appeared", flush=True)
        return
    await _fill(tab, pw_sel, password)
    await asyncio.sleep(0.6)
    print(f"      [school] password field filled: {bool(await _field_value(tab, pw_sel))}", flush=True)
    await _maybe_click(tab, "Sign in")
    await asyncio.sleep(6)

    # "Stay signed in?" -> Yes
    for label in ("Yes", "No"):
        if await _maybe_click(tab, label, timeout=4):
            print(f"      [school] answered '{label}' to stay-signed-in", flush=True)
            break
    await asyncio.sleep(6)


async def wait_inbox(tab, timeout: float = 60) -> bool:
    for _ in range(int(timeout)):
        u = (await _js(tab, "location.href", "")) or ""
        # MUST be a real Outlook mailbox URL, never the MS login page (which also
        # contains "outlook" in its redirect) — that false positive masked a
        # failed login as "inbox ready".
        on_mail = ("outlook" in u and ("/mail" in u or "owa" in u)
                   and "login.microsoftonline" not in u and "login.live" not in u)
        if on_mail:
            if await _js(tab, "!!document.querySelector('[role=main],[aria-label*=Message],div[role=list]')", False):
                return True
        await asyncio.sleep(1)
    return False


async def read_subjects(tab, limit: int = 15) -> list[str]:
    """Best-effort scrape of visible message subjects."""
    raw = await _js(tab, """JSON.stringify(
        [...document.querySelectorAll('[role=option],[role=listitem],[aria-label]')]
        .map(e=>(e.innerText||'').split('\\n')[0].trim())
        .filter(t=>t && t.length>2).slice(0,%d))""" % limit, "[]")
    import json
    try:
        return json.loads(raw) if isinstance(raw, str) else (raw or [])
    except Exception:
        return []


async def run(action: str, timeout: int = 180, exclude_codes: list[str] | None = None) -> int:
    exclude_codes = exclude_codes or []
    email = cfg("SCHOOL_EMAIL")
    pw = cfg("SCHOOL_MAIL_PASSWORD")
    url = cfg("SCHOOL_MAIL_URL", MAIL_URL)
    if not email:
        print("✗ SCHOOL_EMAIL not set"); return 1

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"      [school] launching browser (profile={PROFILE_DIR})", flush=True)
    browser = await uc.start(headless=False, sandbox=False, user_data_dir=str(PROFILE_DIR))
    tab = await browser.get(url)
    await asyncio.sleep(8)

    title = await _js(tab, "document.title", "")
    cur = await _js(tab, "location.href", "")
    print(f"      [school] page: {title!r} @ {cur[:70]}", flush=True)

    # If we landed on a login page, log in (only if password available)
    if any(k in cur for k in ("login.microsoftonline", "login.live", "adfs")) or "sign in" in (title or "").lower():
        if action == "login" and not pw:
            print("      [school] on login page; no password set — log in manually, then Ctrl-C.", flush=True)
            while True:
                await asyncio.sleep(5)
        if pw:
            await do_login(tab, email, pw)
        else:
            print("✗ SCHOOL_MAIL_PASSWORD not set"); return 1

    ok = await wait_inbox(tab, timeout=60)
    print(f"      [school] inbox ready: {ok}", flush=True)

    if action in ("selftest", "login"):
        subs = await read_subjects(tab)
        print("      [school] recent subjects:")
        for s in subs[:12]:
            print(f"        - {s[:80]}")
        if action == "login":
            print("      [school] session kept in profile; browser left open for inspection.")
            while True:
                await asyncio.sleep(5)
        browser.stop()
        return 0

    # otp — the inbox keeps the LAST mail open, so a naive scan returns the SAME
    # (already-used) code forever -> OpenAI "max_check_attempts". Baseline the
    # codes present now and only return a NEW one; also honour --exclude.
    async def _scan() -> list[str]:
        found = []
        subs = await read_subjects(tab)
        for s in subs:
            m = re.search(r"\b(\d{6})\b", s)
            if m and any(k in s.lower() for k in ("openai", "chatgpt", "code", "verif", "confirm")):
                found.append(m.group(1))
        txt = await _js(tab, "document.body ? document.body.innerText : ''", "")
        for m in re.finditer(r"(?:code to continue|verification code)[:\s]*(\d{6})", txt or "", re.I):
            found.append(m.group(1))
        return found

    baseline = set(await _scan())
    skip = set(baseline) | {str(c).strip() for c in exclude_codes if str(c).strip()}
    print(f"      [school] baseline codes present: {sorted(baseline) or 'none'} "
          f"| excluding: {sorted(skip) or 'none'}", flush=True)
    start = time.time()
    while time.time() - start < timeout:
        for code in await _scan():
            if code not in skip:
                print(f"      [school] OTP (new): {code}", flush=True)
                browser.stop()
                return 0
        await asyncio.sleep(6)
    print("      [school] no NEW OTP within timeout", flush=True)
    browser.stop()
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Read school mailbox OTP via browser (nodriver)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("login", help="log in and leave browser open")
    sub.add_parser("selftest", help="log in and list recent subjects")
    o = sub.add_parser("otp", help="wait for an OpenAI OTP")
    o.add_argument("--timeout", type=int, default=180)
    o.add_argument("--exclude", default="", help="comma-separated codes already used (skipped)")
    a = ap.parse_args()
    if not a.cmd:
        ap.print_help(); return 0
    excl = [c for c in (getattr(a, "exclude", "") or "").split(",") if c.strip()]
    return asyncio.run(run(a.cmd, timeout=getattr(a, "timeout", 180), exclude_codes=excl))


if __name__ == "__main__":
    sys.exit(main())
