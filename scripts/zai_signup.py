#!/usr/bin/env python3
"""
scripts/zai_signup.py — create a z.ai account (chat.z.ai / Open WebUI), with the
Aliyun slider solved by scripts/captcha_slider.py (free, no external solver).

Flow: /auth -> Continue with Email -> Sign up -> fill email+password (+name) ->
solve the Aliyun slider -> Create Account -> read the 6-digit code from the
mailbox -> verify -> capture the session token/cookies.

Usage (camoufox venv drives the browser):
  camoufox-venv/bin/python scripts/zai_signup.py --headless
  ... --email me@x --password '...'   # reuse an address you control
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import secrets
import string
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

AUTH = "https://chat.z.ai/auth"
LEDGER = Path.home() / ".config" / "auto-freecf" / "zai_accounts.jsonl"
MAIL_DOMAIN = "kancalabs.biz.id"   # our relay mailbox (biz.id), not gmail


def gen_password(n: int = 14) -> str:
    a = string.ascii_letters + string.digits + "!%*+-_"
    return "".join(random.choice(a) for _ in range(n))


def gen_name() -> str:
    first = random.choice(["alex", "sam", "jordan", "kai", "noah", "mia", "ava", "leo"])
    last = random.choice(["tan", "lee", "kim", "ng", "ong", "wong", "lim", "chan"])
    return f"{first} {last}".title() + str(random.randint(10, 99))


def _jwt_email(tok: str | None) -> str | None:
    """Read the `email` claim from a JWT payload (no verification)."""
    if not tok or tok.count(".") < 2:
        return None
    import base64 as _b64
    try:
        p = tok.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(_b64.urlsafe_b64decode(p)).get("email")
    except Exception:  # noqa: BLE001
        return None


async def _signup_once(*, email: str | None, password: str | None,
                       headless: bool, log) -> dict:
    import mailboxes as M
    import captcha_slider as CS
    from camoufox.async_api import AsyncCamoufox

    box = None
    if not email:
        # Use OUR domains (relay), not gmail -- z.ai treats gmail+tag as throwaway.
        box = M.open_mailbox("auto", domain=MAIL_DOMAIN, log=log)
        email = box.address
    password = password or gen_password()
    name = gen_name()
    rec: dict = {"email": email, "password": password, "provider": "zai"}
    log(f"  email   : {email}")
    log(f"  password: {password}")
    log(f"  name    : {name}")

    async with AsyncCamoufox(headless=headless, humanize=True, os="windows") as browser:
        page = await browser.new_page()
        seen: list[str] = []
        async def _on_resp(r):
            if "/api/v1/auths" in r.url and r.request.method == "POST":
                try:
                    seen.append(await r.text())
                except Exception:  # noqa: BLE001
                    pass
        page.on("response", lambda r: asyncio.create_task(_on_resp(r)))

        await page.goto(AUTH, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(4000)
        await page.get_by_text("Continue with Email").first.click()
        await page.wait_for_timeout(2000)
        await page.get_by_text("Sign up", exact=True).last.click()
        await page.wait_for_timeout(2500)

        # Fill Name / Email / Password (Name appears in signup mode).
        await page.wait_for_timeout(800)
        names = await page.query_selector_all("input")
        descr = []
        for i in names:
            descr.append(f"{(await i.get_attribute('type')) or '?'}:{(await i.get_attribute('placeholder')) or ''}")
        log("  inputs: " + ", ".join(descr))
        nm = (await page.query_selector('input[placeholder*="Name" i]')
              or await page.query_selector('input[type="text"]'))
        if nm:
            await nm.fill(name)
            log("  name filled")
        else:
            log("  ⚠ no Name field found")
        await page.fill('input[type="email"]', email)
        await page.fill('input[type="password"]', password)

        if not await CS.solve_aliyun(page, log=log):
            rec["status"] = "captcha_failed"
            return rec

        for lbl in ("Create Account", "Sign up", "Create account"):
            try:
                await page.get_by_role("button", name=lbl).first.click(timeout=4000)
                break
            except Exception:  # noqa: BLE001
                continue
        await page.wait_for_timeout(6000)

        # Email verification code, if asked.
        body = (await page.text_content("body") or "").lower()
        if box and any(k in body for k in ("verif", "code", "confirm")):
            code = box.code("zai", timeout=180)
            if code:
                log(f"  code: {code}")
                for sel in ('input[inputmode="numeric"]', 'input[name*="code" i]',
                            'input[placeholder*="code" i]'):
                    el = await page.query_selector(sel)
                    if el:
                        await el.fill(code)
                        break
                for lbl in ("Verify", "Continue", "Submit", "Confirm"):
                    try:
                        await page.get_by_role("button", name=lbl).first.click(timeout=3000)
                        break
                    except Exception:  # noqa: BLE001
                        continue
                await page.wait_for_timeout(6000)

        tok = await page.evaluate("()=>{try{return localStorage.getItem('token')}catch(e){return null}}")
        cookies = await page.context.cookies()
        acct = _jwt_email(tok)
        rec.update({"url": page.url, "token": tok, "token_email": acct, "auths": seen[-1:] ,
                    "cookies": "; ".join(f"{c['name']}={c['value']}" for c in cookies
                                         if c.get("domain", "").endswith("z.ai")),
                    "status": "ok" if acct and acct == email else "guest"})
        log(f"  token_email={acct}  url={page.url}")
        return rec


async def signup(*, email: str | None = None, password: str | None = None,
                 headless: bool = True, attempts: int = 6, log=print) -> dict:
    """Retry the whole flow until a REAL account is created (non-guest token)."""
    last: dict = {}
    for i in range(1, attempts + 1):
        log(f"=== attempt {i}/{attempts} ===")
        try:
            rec = await _signup_once(email=email, password=password,
                                     headless=headless, log=log)
        except Exception as e:  # noqa: BLE001
            log(f"  attempt error: {type(e).__name__}: {e}")
            rec = {"status": "error", "error": str(e)}
        last = rec
        if rec.get("status") == "ok":
            return rec
        log(f"  -> status={rec.get('status')}; retrying with a fresh mailbox")
        email = None  # new address next round
        password = None
    return last


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Create a z.ai account (Camoufox + slider solver)")
    ap.add_argument("--email", default=None)
    ap.add_argument("--password", default=None)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("-o", "--out", default=str(LEDGER))
    a = ap.parse_args(argv)
    try:
        rec = asyncio.run(signup(email=a.email, password=a.password, headless=a.headless))
    except Exception as e:  # noqa: BLE001
        print(f"✗ zai signup failed: {type(e).__name__}: {e}")
        return 1
    print(json.dumps({k: v for k, v in rec.items() if k != "cookies"}, indent=2))
    if rec.get("status") == "ok":
        p = Path(a.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        print(f"✓ saved -> {p}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())