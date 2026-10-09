#!/usr/bin/env python3
"""
scripts/abliteration_mint.py — mint ONE real abliteration.ai API key.

The vendor tool (abliteration-bulk-creator) launches its own Chromium and fails
the signup Turnstile on every egress we have. This drives the SAME flow with
Camoufox, whose free in-browser Turnstile solve we already use for TokenHarbor,
and reuses scripts/mailboxes.py for a real @gmail.com inbox.

Flow (matches abliteration's first-party API):
  /sign-up (fill email+password, Turnstile solve) -> 6-digit code from inbox ->
  /console -> GET /api/console/v1/session -> POST
  /api/console/v1/projects/<id>/api-keys  -> secret_key

Usage:
  camoufox-venv/bin/python scripts/abliteration_mint.py            # 1 key
  ... --headless --proxy http://user:pass@host:port
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import re
import secrets
import string
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

ORIGIN = "https://abliteration.ai"
SEL = {
    "email": 'input[type="email"][name="email"], input[name="email"]',
    "password": 'input[type="password"][name="password"], input[name="password"]',
    "code": 'input[name="code"], input[inputmode="numeric"], input[autocomplete="one-time-code"]',
}


def gen_password(n: int = 16) -> str:
    alpha = string.ascii_letters + string.digits + "!%*+-_"
    return "".join(secrets.choice(alpha) for _ in range(n))


def gen_key_name() -> str:
    a = ("cobalt", "vivid", "amber", "quiet", "swift", "lunar", "ember", "frost", "onyx", "zephyr")
    b = ("falcon", "orbit", "harbor", "comet", "river", "cedar", "lynx", "raven", "drift", "prism")
    return f"key-{random.choice(a)}-{random.choice(b)}-{secrets.token_hex(2)}"


async def mint(*, email: str | None = None, headless: bool = True,
               proxy: str | None = None, mailbox_provider: str = "gmail",
               cdp: str | None = None, log=print) -> dict:
    """Create one abliteration account and mint its API key. Returns a record."""
    import mailboxes as M

    box = None
    if not email:
        box = M.open_mailbox(mailbox_provider, log=log)
        email = box.address
    password = gen_password()
    key_name = gen_key_name()
    log(f"  inbox : {email}")
    log(f"  passwd: {password}")

    # Two ways to get a browser that can pass Turnstile: Camoufox (local stealth
    # Firefox, free in-browser solve) or a cloud browser over CDP (a clean
    # residential IP does the solve) via --cdp.
    if cdp:
        from playwright.async_api import async_playwright
        _pw = await async_playwright().start()
        _b = await _pw.chromium.connect_over_cdp(cdp)
        _ctx = _b.contexts[0] if _b.contexts else await _b.new_context()
        # Reuse the page the session already opened (it is on /sign-up and its
        # Turnstile widget is the one the cloud browser solved), else make one.
        page = _ctx.pages[0] if _ctx.pages else await _ctx.new_page()

        async def _close():
            await _b.close()
            await _pw.stop()
    else:
        from camoufox.async_api import AsyncCamoufox
        kw: dict = dict(headless=headless, humanize=True, os="windows")
        if proxy:
            kw["proxy"] = {"server": proxy}
            kw["geoip"] = True
        _cm = AsyncCamoufox(**kw)
        _br = await _cm.__aenter__()
        page = await _br.new_page()

        async def _close():
            await _cm.__aexit__(None, None, None)

    try:
        await page.goto(f"{ORIGIN}/sign-up", wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_selector(SEL["email"], state="visible", timeout=30000)
        await page.fill(SEL["email"], email)
        await page.fill(SEL["password"], password)

        # The submit stays disabled until Turnstile resolves; Camoufox auto-passes
        # it. Target the button NAMED "Create account" (the form also has a Google
        # SSO submit button that .first would grab).
        btn = page.get_by_role("button", name=re.compile(r"create account", re.I)).first
        for _ in range(90):
            if await btn.is_enabled():
                break
            await page.wait_for_timeout(1000)
        else:
            raise RuntimeError("submit never enabled (Turnstile did not solve)")
        log("  turnstile: solved, submitting")
        await btn.click()
        await page.wait_for_timeout(3000)
        body = await page.text_content("body") or ""
        log(f"  after-submit url: {page.url}")
        if "blocked" in body.lower():
            raise RuntimeError("signup blocked (flagged IP) — retry with --proxy (clean residential)")
        log(f"  after-submit: {'code view' if 'code' in body.lower() or 'verif' in body.lower() else 'unknown view'}")

        # 6-digit code from the inbox.
        if box is None:
            raise RuntimeError("--email given but no inbox configured to read the code")
        code = box.code("abliteration", timeout=180)
        if not code:
            raise RuntimeError("no verification code arrived")
        log(f"  code  : {code}")
        await page.wait_for_selector(SEL["code"], state="visible", timeout=60000)
        await page.fill(SEL["code"], code)
        vbtn = page.get_by_role("button", name="Verify").first
        try:
            if await vbtn.count():
                await vbtn.click()
        except Exception:  # noqa: BLE001 - some views auto-advance
            pass

        await page.wait_for_url("**/console**", timeout=90000)
        await page.wait_for_timeout(2500)

        sid = await page.evaluate(
            "async () => { const r = await fetch('/api/console/v1/session',{credentials:'include'});"
            " return {s:r.status, t: await r.text()}; }")
        if sid.get("s") != 200:
            raise RuntimeError(f"session lookup HTTP {sid.get('s')}: {str(sid.get('t'))[:120]}")
        sdata = json.loads(sid["t"])
        pid = ((sdata.get("active_project") or {}).get("id")
               or (sdata.get("project") or {}).get("id")
               or (sdata.get("data") or {}).get("active_project", {}).get("id"))
        if not pid:
            raise RuntimeError("no active project id in session")

        rec = await page.evaluate(
            "async ({pid,name}) => { const r = await fetch('/api/console/v1/projects/'+pid+'/api-keys',"
            "{method:'POST',credentials:'include',headers:{'Content-Type':'application/json',"
            "'Idempotency-Key':(crypto.randomUUID?crypto.randomUUID():String(Date.now()))},"
            "body:JSON.stringify({name})}); return {s:r.status, t: await r.text()}; }",
            {"pid": pid, "name": key_name})
        if rec.get("s") not in (200, 201):
            raise RuntimeError(f"api-key create HTTP {rec.get('s')}: {str(rec.get('t'))[:160]}")
        rdata = json.loads(rec["t"])
        secret = (rdata.get("secret_key") or (rdata.get("api_key") or {}).get("key")
                  or rdata.get("key"))
        if not secret:
            raise RuntimeError(f"no secret in response: {str(rdata)[:160]}")
        log(f"  KEY  : {secret}")
        return {"email": email, "password": password, "api_key": secret,
                "key_name": key_name, "project_id": pid, "provider": "abliteration",
                "base_url": ORIGIN, "status": "ok"}
    finally:
        await _close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Mint one abliteration.ai API key (Camoufox)")
    ap.add_argument("--email", default=None, help="use this address (else a fresh emailmux @gmail.com)")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--proxy", default=None, help="http://user:pass@host:port")
    ap.add_argument("--cdp", default=None, help="attach to a cloud browser (TinyFish/BrightData) instead of Camoufox")
    ap.add_argument("-o", "--out", default=None, help="append the record as JSON to this file")
    a = ap.parse_args(argv)
    try:
        rec = asyncio.run(mint(email=a.email, headless=a.headless, proxy=a.proxy, cdp=a.cdp))
    except Exception as e:  # noqa: BLE001
        print(f"✗ mint failed: {type(e).__name__}: {e}")
        return 1
    print(json.dumps(rec, indent=2))
    if a.out:
        with open(a.out, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())