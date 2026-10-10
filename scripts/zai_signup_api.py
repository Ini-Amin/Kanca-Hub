#!/usr/bin/env python3
"""
scripts/zai_signup_api.py — create a z.ai account by REWRITING the app's submit.

The login form only lets us reach sign-in, and the captcha's `captcha_verify_param`
is single-use and page-bound, so we cannot replay it ourselves. Solution: install a
fetch/XHR hook that rewrites the app's own `POST /auths/signin` into
`POST /auths/signup` (with the required `name`), so the app sends the FRESH param
to the signup endpoint. Then finish via the email token.

signup body : { name, email, password, profile_image_url, sso_redirect, captcha_verify_param }
verify      : POST /auths/verify_email { username, email, token }
finish      : POST /auths/finish_signup { username, email, token, password, ... }
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import string
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

AUTH = "https://chat.z.ai/auth"
LEDGER = Path.home() / ".config" / "auto-freecf" / "zai_accounts.jsonl"

# Installed via add_init_script (runs BEFORE any page JS) so the app's own
# reference to fetch/XHR is the patched one. Rewrites /auths/signin -> /auths/signup.
HOOK = """(() => {
  if (window.__ZAI_HOOK__) return;
  window.__ZAI_HOOK__ = true;
  window.__ZAI_NAME__ = window.__ZAI_NAME__ || '';
  const REWRITE = (url, bodyText) => {
    try {
      if (url && url.includes('/auths/signin') && bodyText) {
        const b = JSON.parse(bodyText);
        if (b && b.email && b.password) {
          const nb = { name: window.__ZAI_NAME__ || 'User', email: b.email,
                       password: b.password, profile_image_url: '', sso_redirect: '',
                       captcha_verify_param: b.captcha_verify_param };
          window.__ZAI_REWROTE__ = nb;
          return [url.replace('/auths/signin', '/auths/signup'), JSON.stringify(nb)];
        }
      }
    } catch (e) { window.__ZAI_ERR__ = String(e); }
    return [url, bodyText];
  };
  const of = window.fetch;
  window.fetch = function(input, init) {
    try {
      if (init && init.body && typeof init.body === 'string') {
        const url = (typeof input === 'string') ? input : (input && input.url) || '';
        const [nu, nb] = REWRITE(url, init.body);
        if (nu !== url) { input = nu; init = Object.assign({}, init, { body: nb }); }
      }
    } catch (e) { window.__ZAI_ERR__ = String(e); }
    return of.call(this, input, init);
  };
  const oo = XMLHttpRequest.prototype.open, os = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(m, u) { this.__zai_url = u; return oo.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function(body) {
    try {
      const [nu, nb] = REWRITE(this.__zai_url || '', typeof body === 'string' ? body : '');
      if (nu !== this.__zai_url) { this.__zai_url = nu; oo.call(this, 'POST', nu, true); body = nb; }
    } catch (e) { window.__ZAI_ERR__ = String(e); }
    return os.call(this, body);
  };
})();"""


def gen_password(n=14):
    return "".join(random.choice(string.ascii_letters + string.digits + "!%*+-_") for _ in range(n))


def gen_name():
    a = random.choice(["alex", "sam", "jordan", "kai", "noah", "mia", "ava", "leo"])
    b = random.choice(["tan", "lee", "kim", "ng", "ong", "wong", "lim", "chan"])
    return f"{a} {b}".title() + str(random.randint(10, 99))


async def run(*, headless=False, puzzles=20, log=print) -> dict:
    import mailboxes as M
    import captcha_slider as CS
    from camoufox.async_api import AsyncCamoufox

    box = M.open_mailbox("auto", domain="kancalabs.biz.id", log=log)
    email, password, name = box.address, gen_password(), gen_name()
    rec = {"email": email, "password": password, "name": name, "provider": "zai", "status": "start"}
    log(f"  email   : {email}\n  password: {password}\n  name    : {name}")

    async with AsyncCamoufox(headless=headless, humanize=True, os="windows") as browser:
        page = await browser.new_page()
        await page.add_init_script(HOOK)          # BEFORE any page JS
        posts = []
        async def on_resp(r):
            if "/auths/" in r.url and r.request.method == "POST":
                try:
                    posts.append((r.url.split("chat.z.ai")[-1], r.request.post_data, r.status, (await r.text())[:200]))
                except Exception:  # noqa: BLE001
                    pass
        page.on("response", lambda r: asyncio.create_task(on_resp(r)))

        await page.goto(AUTH, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(4000)
        await page.get_by_text("Continue with Email").first.click()
        await page.wait_for_timeout(2000)
        await page.fill('input[type="email"]', email)
        await page.fill('input[type="password"]', password)

        if not await CS.solve_aliyun(page, log=log, puzzles=puzzles):
            rec["status"] = "captcha_failed"
            return rec

        await page.evaluate("(n) => { window.__ZAI_NAME__ = n; }", name)  # set name for the rewrite
        # submit (the hook turns signin -> signup)
        for lbl in ("Sign in", "Sign In", "Create Account", "Sign up"):
            try:
                await page.get_by_role("button", name=lbl).first.click(timeout=3500)
                log(f"  clicked: {lbl}")
                break
            except Exception:  # noqa: BLE001
                continue
        await page.wait_for_timeout(6000)

        log(f"  posts: {[(u, s) for u, _, s, _ in posts]}")
        rec["posts"] = [{"url": u, "status": s, "resp": t} for u, _, s, t in posts]

        # find the email verification token from the mailbox and finish
        verify_url = (await page.evaluate("() => window.__ZAI_REWROTE__ ? 'rewrote' : 'no-rewrite'"))
        log(f"  rewrite: {verify_url}")

        tok = box.code("zai", timeout=60)
        link = None
        try:
            link = box.link(r"https?://[^\s]+", timeout=5)
        except Exception:  # noqa: BLE001
            pass
        if tok or link:
            log(f"  mail code={tok} link={link}")
        dbg = await page.evaluate("() => ({rewrote: window.__ZAI_REWROTE__||null, err: window.__ZAI_ERR__||null})")
        rec["rewrote"] = bool(dbg.get("rewrote"))
        rec["hook_err"] = dbg.get("err")
        session_tok = await page.evaluate("()=>localStorage.getItem('token')")
        rec["token"] = session_tok
        rec["status"] = "ok" if session_tok and email in _jwt_email(session_tok or "") else "guest_or_pending"
        log(f"  token_email={_jwt_email(session_tok)}")
        return rec


def _jwt_email(tok):
    import base64
    try:
        p = tok.split(".")[1]; p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p)).get("email")
    except Exception:  # noqa: BLE001
        return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="z.ai signup by rewriting the app's submit")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--puzzles", type=int, default=20)
    a = ap.parse_args(argv)
    rec = asyncio.run(run(headless=a.headless, puzzles=a.puzzles))
    print(json.dumps({k: v for k, v in rec.items() if k not in ("token",)}, indent=2))
    if rec.get("status") == "ok":
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())