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
KEYS_FILE = Path.home() / ".config/auto-freecf/abliteration_keys.txt"
SEL = {
    "email": 'input[type="email"][name="email"], input[name="email"]',
    "password": 'input[type="password"][name="password"], input[name="password"]',
    "code": 'input[name="code"], input[inputmode="numeric"], input[autocomplete="one-time-code"]',
}


def inject_9router(api_key: str, email: str = "") -> bool:
    """Inject minted Abliteration API key directly into 9Router SQLite DB."""
    import sqlite3, uuid
    db_path = Path.home() / ".9router/db/data.sqlite"
    if not db_path.is_file():
        return False
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        now = str(int(time.time() * 1000))
        node_id = "openai-compatible-chat-abliteration"
        node_name = "Abliteration"
        prefix = "ablit"
        base_url = "https://api.abliteration.ai/v1"

        cur.execute("SELECT id FROM providerNodes WHERE id=?", (node_id,))
        if not cur.fetchone():
            ndata = json.dumps({"prefix": prefix, "baseUrl": base_url, "apiType": "chat"})
            cur.execute(
                "INSERT INTO providerNodes (id, type, name, data, createdAt, updatedAt) VALUES (?, ?, ?, ?, ?, ?)",
                (node_id, "openai-compatible", node_name, ndata, now, now),
            )

        cdata = json.dumps({
            "name": f"Abliteration-{email or 'Key'}",
            "apiKey": api_key,
            "defaultModel": "abliterated-model-large-flash",
            "providerSpecificData": {"baseUrl": base_url, "prefix": prefix, "apiType": "chat"},
        })
        cur.execute("SELECT id FROM providerConnections WHERE provider=?", (node_id,))
        existing = cur.fetchone()
        if existing:
            cur.execute(
                "UPDATE providerConnections SET data=?, isActive=1 WHERE provider=?",
                (cdata, node_id),
            )
        else:
            cid = str(uuid.uuid4())
            cur.execute(
                "INSERT INTO providerConnections (id, provider, authType, name, priority, isActive, data, createdAt, updatedAt) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cid, node_id, "apikey", f"Abliteration-{email or 'Key'}", 1, 1, cdata, now, now),
            )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False


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
            # Firefox rejects inline credentials in `server` (CONNECT 407 →
            # NS_ERROR_PROXY_CONNECTION_REFUSED); pass auth as separate fields.
            if "@" in proxy:
                creds, hostport = proxy.rsplit("@", 1)
                user, pwd = creds.split("://", 1)[1].split(":", 1)
                kw["proxy"] = {"server": f"http://{hostport}",
                               "username": user, "password": pwd}
            else:
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
        # page.fill() leaves React state empty on this form ("Enter your email
        # address" on Continue) — click + humanized typing is what registers.
        em = page.locator(SEL["email"])
        await em.click()
        await em.type(email, delay=40)
        # The form is TWO-STEP now: email + Continue reveals the password field.
        # exact=True — the page also has "Continue with Google/GitHub/Microsoft"
        # aria-labels that a substring match would grab instead.
        await page.get_by_role("button", name="Continue", exact=True).click()
        await page.wait_for_selector('input[name="password"]', state="visible", timeout=30000)
        # typed, not fill(): the button stays disabled if the password landed via
        # fill() (same React-state quirk as the email field).
        pw = page.locator('input[name="password"]')
        await pw.click()
        await pw.type(password, delay=30)

        # The submit stays disabled until Turnstile resolves; Camoufox auto-passes
        # it. Target the button NAMED "Create account" (the form also has Google/
        # GitHub/Microsoft SSO submits that a loose match would grab).
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

        # Save to keys file and inject into 9Router
        try:
            KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(KEYS_FILE, "a", encoding="utf-8") as kf:
                kf.write(f"{email}:{secret}\n")
            injected = inject_9router(secret, email=email)
            if injected:
                log("  ✓ Injected Abliteration key directly into 9Router SQLite DB!")
        except Exception as ex:
            log(f"  ⚠️ Warning saving/injecting key: {ex}")

        return {"email": email, "password": password, "api_key": secret,
                "key_name": key_name, "project_id": pid, "provider": "abliteration",
                "base_url": ORIGIN, "status": "ok"}
    finally:
        await _close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Mint one abliteration.ai API key (Camoufox)")
    ap.add_argument("--email", default=None, help="use this address")
    ap.add_argument("--mailbox", default="tempik", help="mailbox provider: tempik, gmail, auto (default: tempik)")
    ap.add_argument("--headful", action="store_true", help="show visible browser window")
    ap.add_argument("--headless", action="store_true", default=True, help="run headless (default)")
    ap.add_argument("--proxy", default=None, help="http://user:pass@host:port")
    ap.add_argument("--cdp", default=None, help="attach to a cloud browser (TinyFish/BrightData) instead of Camoufox")
    ap.add_argument("-o", "--out", default=None, help="append the record as JSON to this file")
    a = ap.parse_args(argv)
    try:
        is_headless = not a.headful if a.headful else a.headless
        rec = asyncio.run(mint(email=a.email, headless=is_headless, proxy=a.proxy, cdp=a.cdp, mailbox_provider=a.mailbox))
    except Exception as e:  # noqa: BLE001
        print(f"✗ mint failed: {type(e).__name__}: {e}")
        return 1
    if a.out:
        with open(a.out, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())