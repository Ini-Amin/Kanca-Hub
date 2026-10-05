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


async def do_login(tab, email: str, password: str) -> None:
    """Walk the Microsoft login flow."""
    print("      [school] completing Microsoft login…", flush=True)
    # email step
    for _ in range(20):
        if await _js(tab, "!!document.querySelector('input[type=email],input[name=loginfmt]')", False):
            break
        await asyncio.sleep(1)
    await _js(tab, """(()=>{const e=document.querySelector('input[type=email],input[name=loginfmt]');
        if(!e) return 0; e.focus();
        const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
        s.call(e,%s); e.dispatchEvent(new InputEvent('input',{bubbles:true,data:%s}));
        return 1;})()""" % (repr(email).replace("'", '"'), repr(email).replace("'", '"')), 0)
    await asyncio.sleep(0.5)
    await _maybe_click(tab, "Next")
    await asyncio.sleep(4)

    # password step
    for _ in range(20):
        if await _js(tab, "!!document.querySelector('input[type=password],input[name=passwd]')", False):
            break
        await asyncio.sleep(1)
    await _js(tab, """(()=>{const e=document.querySelector('input[type=password],input[name=passwd]');
        if(!e) return 0; e.focus();
        const s=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
        s.call(e,%s); e.dispatchEvent(new InputEvent('input',{bubbles:true,data:%s}));
        return 1;})()""" % (repr(password).replace("'", '"'), repr(password).replace("'", '"')), 0)
    await asyncio.sleep(0.5)
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
        u = await _js(tab, "location.href", "")
        if "outlook" in u and ("mail" in u or "owa" in u):
            # inbox rendered?
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


async def run(action: str, timeout: int = 180) -> int:
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

    # otp
    start = time.time()
    while time.time() - start < timeout:
        subs = await read_subjects(tab)
        for s in subs:
            m = re.search(r'\b(\d{6})\b', s)
            if m and any(k in s.lower() for k in ("openai", "chatgpt", "code", "verif", "confirm")):
                print(f"      [school] OTP from subject: {m.group(1)}", flush=True)
                browser.stop()
                return 0
        # also scan page text
        txt = await _js(tab, "document.body ? document.body.innerText : ''", "")
        m = re.search(r'(?:code to continue|verification code)[:\s]*(\d{6})', txt or "", re.I)
        if m:
            print(f"      [school] OTP from page: {m.group(1)}", flush=True)
            browser.stop()
            return 0
        await asyncio.sleep(6)
    print("      [school] no OTP within timeout", flush=True)
    browser.stop()
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Read school mailbox OTP via browser (nodriver)")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("login", help="log in and leave browser open")
    sub.add_parser("selftest", help="log in and list recent subjects")
    o = sub.add_parser("otp", help="wait for an OpenAI OTP")
    o.add_argument("--timeout", type=int, default=180)
    a = ap.parse_args()
    if not a.cmd:
        ap.print_help(); return 0
    return asyncio.run(run(a.cmd, timeout=getattr(a, "timeout", 180)))


if __name__ == "__main__":
    sys.exit(main())
