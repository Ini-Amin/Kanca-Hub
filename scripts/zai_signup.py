#!/usr/bin/env python3
"""
scripts/zai_signup.py — create a z.ai account AND get its real bearer token.

Confirmed flow (live):
  1. /auth -> Continue with Email -> DOM-click the 'Sign up' <button> (toggles the
     SAME form into Create Account mode, revealing a Name field).
     NOTE: a normal Playwright click does NOT fire that button; a DOM .click() does.
  2. Fill Name/Email/Password -> solve the Aliyun slider -> DOM-click 'Create Account'
     -> POST /api/v1/auths/signup  = 200 {"success":true}
  3. Read the verify_email link from the mailbox; open it -> the "Complete
     Registration" page -> set Password + Confirm Password -> click
     'Complete Registration' -> POST /auths/finish_signup -> REAL token issued.

Output record: {email, password, token, ...}. Token is a chat.z.ai bearer JWT.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import random
import string
import sys
import urllib.parse
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


def jwt_email(tok):
    try:
        p = tok.split(".")[1]; p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p)).get("email")
    except Exception:  # noqa: BLE001
        return None


async def _once(*, headless=True, log=print, puzzles=20) -> dict:
    import mailboxes as M
    import captcha_slider as CS
    from camoufox.async_api import AsyncCamoufox

    box = M.open_mailbox("auto", domain=MAIL_DOMAIN, log=log)
    email, password, name = box.address, gen_password(), gen_name()
    rec = {"email": email, "password": password, "name": name, "provider": "zai", "status": "start"}
    log(f"  email: {email}  password: {password}  name: {name}")

    async with AsyncCamoufox(headless=headless, humanize=True, os="windows") as browser:
        page = await browser.new_page()
        posts = []
        async def on_resp(r):
            if "/auths/" in r.url and r.request.method == "POST":
                try:
                    posts.append((r.url.split("chat.z.ai")[-1], r.status, (await r.text())[:120]))
                except Exception:  # noqa: BLE001
                    pass
        page.on("response", lambda r: asyncio.create_task(on_resp(r)))

        # 1-2. signup
        await page.goto(AUTH, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(4000)
        await page.get_by_text("Continue with Email").first.click()
        await page.wait_for_timeout(2000)
        await page.evaluate(_JS_CLICK, "Sign up")
        await page.wait_for_timeout(2500)
        if not await page.query_selector('input[placeholder*="Name" i]'):
            log("  ⚠ Name field not shown")
        nm = await page.query_selector('input[placeholder*="Name" i]')
        if nm:
            await nm.fill(name)
        await page.fill('input[type="email"]', email)
        await page.fill('input[type="password"]', password)
        if not await CS.solve_aliyun(page, log=log, puzzles=puzzles):
            rec["status"] = "captcha_failed"
            return rec
        for lbl in ("Create Account", "Sign up"):
            if await page.evaluate(_JS_CLICK, lbl):
                break
        await page.wait_for_timeout(5000)
        rec["posts"] = [{"url": u, "status": s} for u, s, _ in posts]

        # 3. verify link -> Complete Registration -> real token
        try:
            link = box.link(r"https?://\S+", timeout=150)
        except Exception:  # noqa: BLE001
            link = None
        log(f"  verify link: {(link or '')[:90]}")
        if not link:
            rec["status"] = "no_verify_link"
            return rec
        vq = urllib.parse.parse_qs(urllib.parse.urlparse(link).query)
        vp = await browser.new_page()
        await vp.goto(link, wait_until="domcontentloaded", timeout=60000)
        await vp.wait_for_timeout(6000)
        for el in await vp.query_selector_all('input[type="password"]'):
            try:
                await el.fill(password)
            except Exception:  # noqa: BLE001
                pass
        await vp.evaluate(_JS_CLICK, "Complete Registration")
        await vp.wait_for_timeout(7000)

        tok = await vp.evaluate("()=>localStorage.getItem('token')")
        cookies = await vp.context.cookies()
        acct = jwt_email(tok)
        rec.update({"token": tok, "token_email": acct, "url": vp.url,
                    "cookies": "; ".join(f"{c['name']}={c['value']}" for c in cookies
                                         if "z.ai" in c.get("domain", "")),
                    "verify_token": (vq.get("token") or [""])[0],
                    "status": "ok" if acct == email else "guest_or_pending"})
        log(f"  TOKEN_EMAIL={acct}  url={vp.url}")
        return rec


async def signup(*, headless=True, attempts=3, log=print) -> dict:
    last = {}
    for i in range(1, attempts + 1):
        log(f"=== attempt {i}/{attempts} ===")
        try:
            rec = await _once(headless=headless, log=log)
        except Exception as e:  # noqa: BLE001
            log(f"  error: {type(e).__name__}: {e}")
            rec = {"status": "error", "error": str(e)}
        last = rec
        if rec.get("status") == "ok":
            return rec
    return last


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Create a z.ai account + get its token")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("-o", "--out", default=None, help="write the token to this file")
    a = ap.parse_args(argv)
    rec = asyncio.run(signup(headless=a.headless, attempts=a.attempts))
    print(json.dumps({k: v for k, v in rec.items() if k != "token"}, indent=2))
    if rec.get("status") == "ok":
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        if a.out:
            Path(a.out).write_text(rec["token"])
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())