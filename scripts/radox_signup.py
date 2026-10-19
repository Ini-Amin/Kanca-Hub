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

Promo/voucher flow (--promo CODE, env RADOX_PROMO_CODE): after login and
BEFORE key creation, claim the Pro Plan voucher (350M tokens/mo, no CC).
WARNING: the promo is IP-banned for Indonesia — check_egress_country() must
return a non-ID countryCode (via the same proxy the browser uses) or the run
refuses to claim and exits non-zero. Voucher inventory is FINITE: default
--attempts stays 1; never loop promo claims without checking the ledger for
already-used codes (each code may be one-shot).

Output record: {email, password, name, api_key, status, promo_code_used
(masked first4+last2), plan ('free'|'pro-promo'), promo_detail}.
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
LOGIN = f"{BASE}/login"                      # verified 2026-10-19: Laravel form, #email #password, hidden _token
LEDGER = Path.home() / ".config" / "auto-freecf" / "radox_accounts.jsonl"
PROMO_SUCCESS_RX = re.compile(r"activat|upgrad|success|applied|redeemed|\bpro\b", re.I)
PROMO_ERROR_RX = re.compile(r"invalid|expired|already|used|error|fail", re.I)
# [UNVERIFIED] candidate promo/upgrade surfaces, tried in order.
PROMO_PAGES = ["/dashboard/billing", "/dashboard/subscription", "/dashboard/plans",
               "/dashboard/settings"]
PROMO_INPUT_HINTS = ['input[name*="promo" i]', 'input[name*="voucher" i]',
                     'input[name*="code" i]', 'input[placeholder*="promo" i]',
                     'input[placeholder*="code" i]']
PROMO_BUTTON_LABELS = ["Redeem", "Apply", "Upgrade", "Claim", "Submit"]

MAIL_DOMAIN = os.environ.get("ZAI_MAIL_DOMAIN", "kancalabs.my.id")

# Verified: /dashboard/api-keys is the key page (302->login when anonymous);
# docs say "Dashboard -> API Keys -> Create Key", key shown ONCE, format cc_+48.
KEY_PAGES = [f"{BASE}/dashboard/api-keys", f"{BASE}/dashboard"]
KEY_CREATE_LABELS = ["Create Key", "Create key", "Create API Key"]
# Internal key-create API patterns watched on the network. [UNVERIFIED]
KEY_API_RX = re.compile(r"(api[-_]keys?|keys?)", re.I)
KEY_SECRET_RX = re.compile(r"\b(cc_[A-Za-z0-9]{40,60}|sk-[A-Za-z0-9_\-]{16,})\b")


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

def mask_promo(code: str) -> str:
    return code if len(code) <= 6 else f"{code[:4]}...{code[-2:]}"


def check_egress_country() -> str | None:
    """countryCode of the egress IP via ip-api.com, through the SAME proxy
    the browser uses (HTTP(S)_PROXY env), or direct if none set."""
    import urllib.request
    proxy = _proxy_kwargs().get("proxy")
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy})] if proxy \
        else [urllib.request.ProxyHandler({})]
    opener = urllib.request.build_opener(*handlers)
    with opener.open("http://ip-api.com/json", timeout=15) as r:
        return json.loads(r.read().decode()).get("countryCode")


async def claim_promo(page, code: str, log) -> tuple[bool, str]:
    """Try to redeem `code` on any promo/upgrade surface. Returns (ok, detail).
    Never submits a code more than twice — vouchers may be rate-limited or
    one-shot. All surfaces below are [UNVERIFIED] guesses."""
    submits = 0
    for path in PROMO_PAGES:  # a) dedicated billing/plans pages [UNVERIFIED]
        try:
            resp = await page.goto(f"{BASE}{path}", wait_until="domcontentloaded",
                                   timeout=15000)
            if resp and resp.status == 404:
                continue
        except Exception:  # noqa: BLE001
            continue
        for sel in PROMO_INPUT_HINTS:  # b) promo input+button in page DOM
            inp = page.locator(sel).first
            if not await inp.count():
                continue
            if submits >= 2:
                return False, "promo submit budget exhausted"
            try:
                await inp.fill(code, timeout=5000)
                for lbl in PROMO_BUTTON_LABELS:
                    btn = page.get_by_role("button", name=lbl).first
                    if not await btn.count():
                        continue
                    await btn.click(timeout=5000)
                    submits += 1
                    await page.wait_for_timeout(4000)
                    detail = await js(page, "document.body.innerText", "") or ""
                    m = PROMO_ERROR_RX.search(detail)
                    if m:
                        return False, m.group(0).lower()
                    if PROMO_SUCCESS_RX.search(detail):
                        return True, f"redeemed on {path}"
                    break
            except Exception:  # noqa: BLE001
                continue
    return False, "no promo surface found"


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
            for lbl in ("Create Key", "Create key", "Create API Key", "Generate"):
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


async def _once(*, headless=True, promo=None, log=print) -> dict:
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

        # 3.5 promo/voucher claim — BEFORE key creation
        rec["plan"] = "free"
        if promo:
            cc = None
            try:
                cc = check_egress_country()
            except Exception as e:  # noqa: BLE001
                log(f"  egress check error: {e}")
            log(f"  egress country: {cc}")
            if cc == "ID":
                rec["status"] = "promo_refused_id_ip"
                rec["promo_code_used"] = mask_promo(promo)
                return rec
            ok, det = await claim_promo(page, promo, log)
            rec["promo_code_used"] = mask_promo(promo)
            rec["promo_detail"] = det
            if ok:
                rec["plan"] = "pro-promo"
            else:
                log(f"  promo claim failed: {det}")
        # 4. API key
        key, note = await _grab_api_key(page, net_keys, log)
        rec["api_key"] = key
        rec["api_key_note"] = note
        rec["url"] = page.url
        rec["status"] = "ok" if key else "no_api_key"
        return rec


async def signup(*, headless=True, attempts=3, promo=None, log=print) -> dict:
    last = {}
    for i in range(1, attempts + 1):
        log(f"=== attempt {i}/{attempts} ===")
        try:
            rec = await _once(headless=headless, promo=promo, log=log)
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
    ap.add_argument("--promo", default=os.environ.get("RADOX_PROMO_CODE"),
                    help="promo/voucher code to claim after login (env RADOX_PROMO_CODE)")
    ap.add_argument("-o", "--out", default=None, help="write the API key to this file")
    ap.add_argument("--out-json", default=None,
                    help="write the full record (incl. email/password) to this JSON file")
    a = ap.parse_args(argv)
    if a.promo:
        cc = check_egress_country()
        print(f"egress country: {cc}")
        if cc == "ID":
            print("refusing: egress IP is in Indonesia (ID) — promo is ID-banned; "
                  "use a non-ID VPN/proxy")
            LEDGER.parent.mkdir(parents=True, exist_ok=True)
            with open(LEDGER, "a", encoding="utf-8") as f:
                f.write(json.dumps({"provider": "radox",
                                    "status": "promo_refused_id_ip",
                                    "promo_code_used": mask_promo(a.promo),
                                    "ts": __import__("time").strftime(
                                        "%Y-%m-%dT%H:%M:%S")}) + "\n")
            return 2
    rec = asyncio.run(signup(headless=a.headless,
                             attempts=1 if a.promo else a.attempts, promo=a.promo))
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
    if rec.get("status") == "promo_refused_id_ip":
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
