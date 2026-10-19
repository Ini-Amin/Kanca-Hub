#!/usr/bin/env python3
"""
scripts/radox_signup.py — create a radoxai.com account and capture its API key.

Confirmed flow (register HTML fetched 2026-10-19, plain Laravel form):
  1. GET https://radoxai.com/register — server-rendered form POSTing to the
     same URL with a hidden CSRF `_token`; fields #name #email #phone
     (optional) #password #password_confirmation. No OAuth needed.
  2. Verification email to the Tempik keyless inbox (ZAI_MAIL_DOMAIN=
     kancalabs.my.id); open the verify link if one arrives (radox may also
     accept a 6-digit code — both paths handled).
  3. Log in at /login (same Laravel shape) [UNVERIFIED selectors], then hit
     the dashboard API-keys page. Watch network for an internal key-create
     API (e.g. POST /api/keys, /dashboard/api-keys); prefer it over DOM.

Output record: {email, password, name, api_key, status}.
API base for the failover pool: https://radoxai.com/v1 (OpenAI-compatible).
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
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from camoufox_helpers import js, type_into  # noqa: E402

BASE = "https://radoxai.com"
REGISTER = f"{BASE}/register"
LOGIN = f"{BASE}/login"                      # [UNVERIFIED] path guessed, form shape same stack
LEDGER = Path.home() / ".config" / "auto-freecf" / "radox_accounts.jsonl"
MAIL_DOMAIN = os.environ.get("ZAI_MAIL_DOMAIN", "kancalabs.my.id")

# Dashboard key-page candidates, tried in order. [UNVERIFIED]
KEY_PAGES = [f"{BASE}/dashboard/api-keys", f"{BASE}/dashboard/keys",
             f"{BASE}/api-keys", f"{BASE}/dashboard"]
# Internal key-create API patterns watched on the network. [UNVERIFIED]
KEY_API_RX = re.compile(r"(api[-_]keys?|keys?)", re.I)
KEY_SECRET_RX = re.compile(r"\b(sk-[A-Za-z0-9_\-]{16,}|rad[A-Za-z0-9_\-]{16,})\b")


def gen_password(n=14):
    a = string.ascii_letters + string.digits + "!%*+-_"
    return "".join(random.choice(a) for _ in range(n))


def gen_name():
    a = random.choice(["alex", "sam", "jordan", "kai", "noah", "mia", "ava", "leo"])
    b = random.choice(["tan", "lee", "kim", "ng", "ong", "wong", "lim", "chan"])
    return f"{a} {b}".title() + str(random.randint(10, 99))


def _proxy_kwargs():
    """Honor HTTP(S)_PROXY like zai_signup's camoufox launch."""
    p = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") \
        or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    return {"proxy": p} if p else {}


async def _solve_captcha(page, log):
    import captcha_slider as CS
    from camoufox_helpers import detect_challenge
    ch = await detect_challenge(page)
    if not ch:
        return True
    log(f"  challenge: {ch}")
    try:
        if await CS.solve_aliyun(page, log=log):
            return True
    except Exception as e:  # noqa: BLE001
        log(f"  slider error: {e}")
    return False


async def _submit_form(page, url: str, values: dict, log) -> bool:
    """Fill a Laravel form by CSS id and submit; returns True if navigated."""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(2500)
        for sel, val in values.items():
            if not await type_into(page, f"input#{sel}", val, label=sel):
                # tel input is optional; missing fields other than phone = fail
                if sel != "phone":
                    log(f"  ⚠ field #{sel} not found")
                    return False
        await _solve_captcha(page, log)
        btn = page.locator('button[type="submit"]').first
        await btn.click(timeout=8000)
        await page.wait_for_timeout(4000)
        return True
    except Exception as e:  # noqa: BLE001
        log(f"  form error: {type(e).__name__}: {e}")
        return False


async def _logged_in(page) -> bool:
    url = page.url
    return "register" not in url and "login" not in url


async def _grab_api_key(page, net_keys: list, log):
    """API key via (1) captured key-create API response, (2) dashboard DOM,
    (3) localStorage. Returns (key_or_None, note)."""
    if net_keys:
        for blob in net_keys:
            m = KEY_SECRET_RX.search(blob)
            if m:
                return m.group(1), "network key-create response"
    for url in KEY_PAGES:
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
            body = await js(page, "document.body.innerText", "") or ""
            m = KEY_SECRET_RX.search(body)
            if m:
                return m.group(1), f"DOM {url}"
            # try clicking a create button, then re-scan body + network
            for lbl in ("Create", "Generate", "New API Key", "Create API Key"):
                loc = page.get_by_text(lbl, exact=False).first
                if await loc.count():
                    try:
                        await loc.click(timeout=4000)
                        await page.wait_for_timeout(4000)
                    except Exception:  # noqa: BLE001
                        pass
                    body = await js(page, "document.body.innerText", "") or ""
                    m = KEY_SECRET_RX.search(body)
                    if m:
                        return m.group(1), f"DOM after {lbl} @ {url}"
                    if net_keys:
                        for blob in net_keys:
                            m = KEY_SECRET_RX.search(blob)
                            if m:
                                return m.group(1), f"network after {lbl}"
                    break
        except Exception:  # noqa: BLE001
            continue
    tok = await js(page, "JSON.stringify(localStorage)", "") or ""
    m = KEY_SECRET_RX.search(tok)
    if m:
        return m.group(1), "localStorage"
    return None, "no key surface found"


async def _once(*, headless=True, log=print) -> dict:
    import mailboxes as M
    from camoufox.async_api import AsyncCamoufox

    box = M.open_mailbox("auto", domain=MAIL_DOMAIN, log=log)
    email, password, name = box.address, gen_password(), gen_name()
    rec = {"email": email, "password": password, "name": name,
           "provider": "radox", "status": "start", "api_key": None}
    log(f"  email: {email}  password: {password}  name: {name}")

    async with AsyncCamoufox(headless=headless, humanize=True, os="windows",
                             **_proxy_kwargs()) as browser:
        page = await browser.new_page()
        net_keys: list[str] = []

        async def on_resp(r):
            try:
                if KEY_API_RX.search(r.url) or "key" in (r.url or ""):
                    net_keys.append((await r.text())[:4000])
            except Exception:  # noqa: BLE001
                pass
        page.on("response", lambda r: asyncio.create_task(on_resp(r)))

        # 1. register
        ok = await _submit_form(page, REGISTER, {
            "name": name, "email": email, "phone": "",
            "password": password, "password_confirmation": password,
        }, log)
        if not ok:
            rec["status"] = "register_form_error"
            return rec
        err = await js(page, "document.body.innerText", "") or ""
        if "email has already" in err or "invalid" in err.lower()[:2000]:
            rec["status"] = "register_rejected"
            return rec

        # 2. verification: link first, then 6-digit code
        link = None
        try:
            link = box.link(r"https?://\S*radoxai\S*", timeout=150)
        except Exception:  # noqa: BLE001
            pass
        log(f"  verify link: {(link or '')[:90]}")
        if link:
            vp = await browser.new_page()
            try:
                await vp.goto(link, wait_until="domcontentloaded", timeout=60000)
                await vp.wait_for_timeout(5000)
                await _solve_captcha(vp, log)
                for lbl in ("Verify", "Confirm", "Verify Email"):
                    loc = vp.get_by_text(lbl, exact=False).first
                    if await loc.count():
                        await loc.click(timeout=5000)
                        break
                await vp.wait_for_timeout(3000)
            except Exception as e:  # noqa: BLE001
                log(f"  verify nav error: {e}")
        else:
            code = None
            try:
                code = box.code(vendor="radox", timeout=90)
            except Exception:  # noqa: BLE001
                pass
            if code:
                ci = page.locator('input[maxlength="6"], input[name*="code" i]').first
                if await ci.count():
                    await ci.fill(code)
                    await page.keyboard.press("Enter")
                    await page.wait_for_timeout(4000)
                else:
                    log("  got code but no code input visible")
            else:
                rec["status"] = "no_verify_email"
                return rec

        # 3. login (fresh page, session may not carry over)
        await _submit_form(page, LOGIN, {
            "email": email, "password": password,
        }, log)
        if not await _logged_in(page):
            # maybe login selectors differ; try name=password field pair anyway
            rec["status"] = "login_failed"
            return rec

        # 4. API key
        key, note = await _grab_api_key(page, net_keys, log)
        rec["api_key"] = key
        rec["api_key_note"] = note
        rec["url"] = page.url
        rec["status"] = "ok" if key else "no_api_key"
        return rec


async def signup(*, headless=True, attempts=3, log=print) -> dict:
    last = {}
    for i in range(1, attempts + 1):
        log(f"=== attempt {i}/{attempts} ===")
        try:
            rec = await _once(headless=headless, log=log)
        except Exception as e:  # noqa: BLE001
            log(f"  error: {type(e).__name__}: {e}")
            rec = {"status": "error", "error": str(e), "api_key": None}
        last = rec
        if rec.get("status") == "ok":
            return rec
    return last


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Create a radoxai.com account + get its API key")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("-o", "--out", default=None, help="write the API key to this file")
    ap.add_argument("--out-json", default=None,
                    help="write the full record (incl. email/password) to this JSON file")
    a = ap.parse_args(argv)
    rec = asyncio.run(signup(headless=a.headless, attempts=a.attempts))
    if a.out_json:
        Path(a.out_json).write_text(json.dumps(rec))
    print(json.dumps({k: v for k, v in rec.items() if k != "api_key"}, indent=2))
    if rec.get("status") == "ok":
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        if a.out and rec.get("api_key"):
            Path(a.out).write_text(rec["api_key"])
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
