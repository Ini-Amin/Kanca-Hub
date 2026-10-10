#!/usr/bin/env python3
"""
scripts/zai_signup.py — create a z.ai account (chat.z.ai / Open WebUI).

Flow (confirmed live):
  /auth -> Continue with Email -> JS-click the 'Sign up' leaf button (this toggles
  the SAME form into Create Account mode, adding a Name field) -> fill Name/Email/
  Password -> solve the Aliyun slider -> JS-click 'Create Account' -> read the email
  token -> verify.

Key: the mode toggle is a plain <button class='font-medium underline'>; a normal
Playwright click does NOT trigger it, but a DOM .click() does. Same for the submit.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import secrets
import string
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

AUTH = "https://chat.z.ai/auth"
LEDGER = Path.home() / ".config" / "auto-freecf" / "zai_accounts.jsonl"
MAIL_DOMAIN = "kancalabs.biz.id"

_JS_CLICK = """(text) => {
    const els=[...document.querySelectorAll('button,a,span,div')]
      .filter(e => (e.innerText||'').trim() === text && e.children.length === 0);
    if (!els.length) return false;
    els[els.length-1].click(); return true;
}"""


def gen_password(n=14):
    a = string.ascii_letters + string.digits + "!%*+-_"
    return "".join(random.choice(a) for _ in range(n))


def gen_name():
    a = random.choice(["alex", "sam", "jordan", "kai", "noah", "mia", "ava", "leo"])
    b = random.choice(["tan", "lee", "kim", "ng", "ong", "wong", "lim", "chan"])
    return f"{a} {b}".title() + str(random.randint(10, 99))


def _jwt_email(tok):
    import base64
    try:
        p = tok.split(".")[1]; p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p)).get("email")
    except Exception:  # noqa: BLE001
        return None


async def _signup_once(*, email=None, password=None, headless=True, log=print) -> dict:
    import mailboxes as M
    import captcha_slider as CS
    from camoufox.async_api import AsyncCamoufox

    box = None
    if not email:
        box = M.open_mailbox("auto", domain=MAIL_DOMAIN, log=log)
        email = box.address
    password = password or gen_password()
    name = gen_name()
    rec = {"email": email, "password": password, "name": name, "provider": "zai"}
    log(f"  email: {email}  password: {password}  name: {name}")

    async with AsyncCamoufox(headless=headless, humanize=True, os="windows") as browser:
        page = await browser.new_page()
        posts = []
        async def on_resp(r):
            if "/auths/" in r.url and r.request.method == "POST":
                try:
                    posts.append((r.url.split("chat.z.ai")[-1], r.status, (await r.text())[:200]))
                except Exception:  # noqa: BLE001
                    pass
        page.on("response", lambda r: asyncio.create_task(on_resp(r)))

        await page.goto(AUTH, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(4000)
        await page.get_by_text("Continue with Email").first.click()
        await page.wait_for_timeout(2000)

        # Toggle the SAME form into Create Account mode (adds the Name field).
        ok = await page.evaluate(_JS_CLICK, "Sign up")
        log(f"  toggle Sign up: {ok}")
        await page.wait_for_timeout(2500)
        phs = await page.evaluate("()=>[...document.querySelectorAll('input')].map(e=>e.getAttribute('placeholder'))")
        log(f"  inputs: {phs}")

        nm = await page.query_selector('input[placeholder*="Name" i]')
        if nm:
            await nm.fill(name)
        await page.fill('input[type="email"]', email)
        await page.fill('input[type="password"]', password)

        if not await CS.solve_aliyun(page, log=log, puzzles=20):
            rec["status"] = "captcha_failed"
            return rec

        # Submit (DOM click; the mode is now Create Account).
        for lbl in ("Create Account", "Sign up", "Sign in"):
            if await page.evaluate(_JS_CLICK, lbl):
                log(f"  submitted: {lbl}")
                break
        await page.wait_for_timeout(6000)
        rec["posts"] = [{"url": u, "status": s, "resp": t} for u, s, t in posts]

        # Email verification token, if the server sent one.
        if box:
            code = box.code("zai", timeout=120)
            link = None
            try:
                link = box.link(r"https?://\S+", timeout=5)
            except Exception:  # noqa: BLE001
                pass
            if code or link:
                log(f"  mail code={code} link={link}")
                # Verify via the API: POST /auths/verify_email {username,email,token}
                if link:
                    import urllib.parse as _up
                    q = _up.parse_qs(_up.urlparse(link).query)
                    vtok = (q.get("token") or [""])[0]
                    vmail = (q.get("email") or [email])[0]
                    vuser = (q.get("username") or [name])[0]
                    if vtok:
                        r = await page.evaluate(
                            """async ({username,email,token}) => {
                                const r = await fetch('/api/v1/auths/verify_email',{method:'POST',
                                  credentials:'include',headers:{'Content-Type':'application/json'},
                                  body: JSON.stringify({username,email,token})});
                                return {s:r.status, t:(await r.text()).slice(0,200)};
                            }""", {"username": vuser, "email": vmail, "token": vtok})
                        log(f"  verify_email: {r}")
                        await page.wait_for_timeout(2000)
                    try:
                        await page.goto(link, wait_until="domcontentloaded", timeout=60000)
                        await page.wait_for_timeout(3000)
                    except Exception:  # noqa: BLE001
                        pass
                    if code:
                        for sel in ('input[inputmode="numeric"]', 'input[placeholder*="code" i]'):
                            el = await page.query_selector(sel)
                            if el:
                                await el.fill(code)
                                break
                        for lbl in ("Verify", "Continue", "Submit", "Create Account"):
                            if await page.evaluate(_JS_CLICK, lbl):
                                break
                        await page.wait_for_timeout(5000)

        tok = await page.evaluate("()=>localStorage.getItem('token')")
        acct = _jwt_email(tok)
        rec.update({"url": page.url, "token": tok, "token_email": acct,
                    "status": "ok" if acct and acct == email else "guest_or_pending"})
        log(f"  token_email={acct}  url={page.url}")
        return rec


async def signup(*, email=None, password=None, headless=True, attempts=3, log=print) -> dict:
    last = {}
    for i in range(1, attempts + 1):
        log(f"=== attempt {i}/{attempts} ===")
        try:
            rec = await _signup_once(email=email, password=password, headless=headless, log=log)
        except Exception as e:  # noqa: BLE001
            log(f"  attempt error: {type(e).__name__}: {e}")
            rec = {"status": "error", "error": str(e)}
        last = rec
        if rec.get("status") == "ok":
            return rec
        email = password = None
    return last


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Create a z.ai account")
    ap.add_argument("--email", default=None)
    ap.add_argument("--password", default=None)
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--attempts", type=int, default=3)
    a = ap.parse_args(argv)
    rec = asyncio.run(signup(email=a.email, password=a.password, headless=a.headless, attempts=a.attempts))
    print(json.dumps({k: v for k, v in rec.items() if k != "token"}, indent=2))
    if rec.get("status") == "ok":
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())