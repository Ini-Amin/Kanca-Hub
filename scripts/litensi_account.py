#!/usr/bin/env python3
"""
scripts/litensi_account.py — register/login a Litensi account (Camoufox + relay mail).

Litensi signs up with a Laravel form (username/email/password) guarded by a
Cloudflare Turnstile. A genuine Camoufox browser auto-passes the Turnstile, so we
can register with a kancalabs relay mailbox, then log in. Credentials are saved to
a ledger so the flow is repeatable (the site is flaky).

Usage (camoufox venv):
  camoufox-venv/bin/python scripts/litensi_account.py register --headless
  camoufox-venv/bin/python scripts/litensi_account.py login
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import string
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

LEDGER = Path.home() / ".config" / "auto-freecf" / "litensi_account.json"
REGISTER_URL = "https://litensi.id/register"
LOGIN_URL = "https://litensi.id/login"


def _gen_username() -> str:
    return "lit" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))


def _gen_password() -> str:
    return "Lt" + random.choice("!@#$%^&*") + "".join(
        random.choices(string.ascii_letters + string.digits, k=10)) + "9!"


def load_account() -> dict:
    try:
        return json.loads(LEDGER.read_text())
    except Exception:
        return {}


def save_account(**kw) -> None:
    data = load_account()
    data.update(kw)
    data["updated"] = int(time.time())
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(data, indent=2))
    LEDGER.chmod(0o600)


async def _screenshot(page, tag: str) -> None:
    if os.environ.get("LITENSI_DEBUG"):
        try:
            p = Path("/tmp/opencode") / f"litensi_{tag}.png"
            await page.screenshot(path=str(p), full_page=True)
            print(f"    (shot -> {p})")
        except Exception:
            pass


async def register(*, headless: bool, timeout: float) -> dict:
    import github_farm as g
    import residential_proxy_signup as R
    from camoufox.async_api import AsyncCamoufox

    domain = g.get_relay_config()["domains"][0]
    mailbox = g.create_relay_mailbox(domain=domain)
    email = mailbox["email"]
    username = _gen_username()
    password = _gen_password()

    print(f"  • Email    : {email}")
    print(f"  • Username : {username}")
    print(f"  • Password : {password}")

    async with AsyncCamoufox(headless=headless, os="windows", humanize=True) as browser:
        page = await browser.new_page()
        await page.goto(REGISTER_URL, wait_until="domcontentloaded", timeout=60000)
        # wait out a possible Turnstile / "just a moment"
        for _ in range(20):
            await asyncio.sleep(2)
            if await page.locator("input[name='username']").count() > 0:
                break
        await R._dismiss_overlays(page)

        await _fill_name(page, "username", username)
        await _fill_name(page, "email", email)
        await _fill_name(page, "password", password)
        await _fill_name(page, "password_confirmation", password)
        await asyncio.sleep(1.0)
        await _screenshot(page, "register_filled")

        clicked = await R._click_text(page, "Sign Up", "Sign up", "Register")
        print(f"  → clicked {clicked}; waiting for Turnstile + redirect...")

        waited = 0.0
        while waited < timeout:
            await asyncio.sleep(2)
            waited += 2
            url = page.url or ""
            if "/register" not in url and "/login" not in url:
                break
            alert = await R._page_alert(page)
            if alert and any(w in alert.lower() for w in ("already", "invalid", "error", "taken")):
                print(f"  ✗ register alert: {alert}")
                return {"status": "register_alert", "error": alert,
                        "email": email, "username": username, "password": password}

        await _screenshot(page, "register_after")
        status = "registered" if "/register" not in (page.url or "") else "no_redirect"
        print(f"  {'✓' if status == 'registered' else '✗'} {status} @ {page.url}")
        return {"status": status, "email": email, "username": username, "password": password,
                "jwt": mailbox.get("jwt", "")}


async def _fill_name(page, name: str, value: str) -> bool:
    loc = page.locator(f"input[name='{name}']").first
    if await loc.count() == 0:
        return False
    try:
        await loc.click(timeout=5000)
        await loc.fill(value)
    except Exception:
        await loc.evaluate("""(el, v) => { el.focus(); el.value = v;
            el.dispatchEvent(new Event('input',{bubbles:true}));
            el.dispatchEvent(new Event('change',{bubbles:true})); }""", value)
    return True


async def login(*, headless: bool, username: str = "", password: str = "", timeout: float = 60) -> dict:
    import residential_proxy_signup as R
    from camoufox.async_api import AsyncCamoufox

    acct = load_account()
    username = username or acct.get("username") or acct.get("email") or ""
    password = password or acct.get("password") or ""
    if not username or not password:
        print("  ✗ no credentials (run 'register' first or pass --username/--password)")
        return {"status": "no_credentials"}

    async with AsyncCamoufox(headless=headless, os="windows", humanize=True) as browser:
        page = await browser.new_page()
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        for _ in range(20):
            await asyncio.sleep(2)
            if await page.locator("input[name='password']").count() > 0:
                break
        await R._dismiss_overlays(page)
        await _fill_name(page, "username", username)
        await _fill_name(page, "password", password)
        await asyncio.sleep(0.8)
        await _screenshot(page, "login_filled")

        clicked = await R._click_text(page, "Sign In", "Login", "Log in")
        print(f"  → clicked {clicked}; waiting for dashboard...")

        waited = 0.0
        while waited < timeout:
            await asyncio.sleep(2)
            waited += 2
            if "/login" not in (page.url or ""):
                break
            alert = await R._page_alert(page)
            if alert and any(w in alert.lower() for w in ("invalid", "incorrect", "wrong", "error")):
                return {"status": "login_alert", "error": alert}
        await _screenshot(page, "login_after")
        ok = "/login" not in (page.url or "")
        print(f"  {'✓ logged in' if ok else '✗ login failed'} @ {page.url}")
        return {"status": "logged_in" if ok else "login_failed", "url": page.url}


VERIFY_LINK_RE = None
CODE_RE = None
try:
    import re as _re
    VERIFY_LINK_RE = _re.compile(r"https://litensi\.id/otp/verify/[^\s\)\]\"<>]+")
    CODE_RE = _re.compile(r"\b(\d{6})\b")
    _TAG_RE = _re.compile(r"<[^>]+>")
except Exception:
    pass


def _plain(mail: dict) -> str:
    body = " ".join(str(mail.get(k) or "") for k in ("subject", "text", "body", "html", "source"))
    return _TAG_RE.sub(" ", body) if _TAG_RE else body


def _verify_link(mails: list[dict]) -> str | None:
    """Registration 'Verify Email Address' link — newest first."""
    for m in mails:
        hit = VERIFY_LINK_RE.search(_plain(m))
        if hit:
            return hit.group(0)
    return None


def _code_from_relay(mails: list[dict], *, after: float = 0.0) -> str | None:
    """6-digit confirmation code from the newest email (optionally only new mail)."""
    for m in mails:
        ts = m.get("timestamp") or m.get("created_at") or 0
        try:
            if after and float(ts) and float(ts) < after:
                continue
        except Exception:
            pass
        hit = CODE_RE.search(_plain(m))
        if hit:
            return hit.group(1)
    return None


async def _do_login(page, R, username: str, password: str, timeout: float = 40) -> bool:
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
    for _ in range(20):
        await asyncio.sleep(2)
        if await page.locator("input[name='password']").count() > 0:
            break
    await R._dismiss_overlays(page)
    await _fill_name(page, "username", username)
    await _fill_name(page, "password", password)
    await R._click_text(page, "Sign In", "Login")
    waited = 0.0
    while waited < timeout:
        await asyncio.sleep(2)
        waited += 2
        if "/login" not in (page.url or ""):
            return True
    return "/login" not in (page.url or "")


async def unlock_api(*, headless: bool, timeout: float) -> dict:
    """Confirm the account email, then read the API-developer page (/profile/api).

    Order matters: following the registration verify link LOGS THE SESSION OUT,
    so we verify first, then sign in fresh, then open /profile/api. That page is
    separately gated by a one-time 6-digit email step-up (our relay mailbox).
    """
    import github_farm as g
    import residential_proxy_signup as R
    from camoufox.async_api import AsyncCamoufox

    acct = load_account()
    if not acct.get("username") or not acct.get("password"):
        return {"status": "no_credentials"}
    jwt = acct.get("relay_jwt", "")

    async with AsyncCamoufox(headless=headless, os="windows", humanize=True) as browser:
        page = await browser.new_page()

        # Step 1: confirm the account email via the registration link (if pending).
        try:
            link = _verify_link(g.poll_relay_inbox(jwt))
        except Exception:
            link = None
        if link:
            print("  → confirming account email via registration link...")
            await page.goto(link, wait_until="domcontentloaded", timeout=60000)
            await asyncio.sleep(2)
            print(f"    -> {page.url}")

        # Step 2: sign in fresh (the verify link logged the previous session out).
        if not await _do_login(page, R, acct["username"], acct["password"]):
            return {"status": "login_failed", "url": page.url}
        print("  ✓ logged in")

        # Step 3: open the API page; a session step-up code may still gate it.
        await page.goto("https://litensi.id/profile/api", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(2)

        if "/security/email" in page.url:
            print("  ! API page gated by email confirmation — requesting code...")
            requested_at = time.time()
            await R._click_text(page, "Kirim kode ke email", "Send code", "Send")
            code = None
            deadline = time.time() + timeout
            while time.time() < deadline and not code:
                try:
                    code = _code_from_relay(g.poll_relay_inbox(jwt), after=requested_at)
                except Exception as e:
                    print(f"    (relay poll error: {e})")
                if not code:
                    await asyncio.sleep(4)
            if not code:
                print("  ✗ no confirmation code received")
                return {"status": "no_code"}
            print(f"  ✓ confirmation code: {code}")
            await _enter_code(page, code)
            # "Verifikasi dan lanjutkan" — NOT "Kirim ulang kode" (resend).
            await R._click_text(page, "Verifikasi dan lanjutkan", "Verifikasi", "Lanjutkan")
            for _ in range(20):
                await asyncio.sleep(2)
                if "/security/email" not in page.url:
                    break
            if "/security/email" in (page.url or ""):
                alert = await R._page_alert(page)
                print(f"  ! still on /security/email (alert={alert!r})")

        await page.goto("https://litensi.id/profile/api", wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(2)
        data = await page.evaluate("""() => ({
            url: location.href,
            inputs: [...document.querySelectorAll('input,textarea,code')].map(i=>({name:i.name,v:(i.value||i.innerText||'').slice(0,120)})),
            body: document.body.innerText.slice(0,1500),
        })""")
        print("  URL:", data["url"])
        print("  body:", data["body"].replace("\n", " | ")[:900])
        await _screenshot(page, "api_unlocked")
        return {"status": "api_page", "url": data["url"], "body": data["body"],
                "inputs": data["inputs"]}


async def _enter_code(page, code: str) -> bool:
    """Type the 6-digit code, handling either a single input or N digit boxes."""
    # dedicated single-input candidates first
    for name in ("code", "otp", "token", "verification_code", "email_code"):
        loc = page.locator(f"input[name='{name}']").first
        if await loc.count() > 0 and await loc.is_editable():
            await loc.fill(code)
            return True
    # split boxes (each maxlength=1) — deterministically the code-entry widgets
    boxes = page.locator("input[maxlength='1']")
    n = await boxes.count()
    if n >= len(code):
        for i, digit in enumerate(code):
            await boxes.nth(i).fill(digit)
            await asyncio.sleep(0.1)
        return True
    # fallback: the last visible text input on the page
    texts = page.locator("input[type='text']")
    if await texts.count() > 0:
        await texts.last.fill(code)
        return True
    return False


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Register/login a Litensi account (Camoufox)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("register", help="create a new account via relay mail")
    r.add_argument("--headless", action="store_true")
    r.add_argument("--timeout", type=float, default=60)
    l = sub.add_parser("login", help="log in with the saved account")
    l.add_argument("--headless", action="store_true")
    l.add_argument("--username", default="")
    l.add_argument("--password", default="")
    l.add_argument("--timeout", type=float, default=60)
    u = sub.add_parser("unlock-api", help="confirm email then read API keys")
    u.add_argument("--headless", action="store_true")
    u.add_argument("--timeout", type=float, default=180)
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.cmd == "unlock-api":
        res = asyncio.run(unlock_api(headless=a.headless, timeout=a.timeout))
        return 0 if res.get("status") == "api_page" else 2
    if a.cmd == "register":
        res = asyncio.run(register(headless=a.headless, timeout=a.timeout))
        if res.get("username"):
            save_account(username=res["username"], email=res["email"],
                         password=res["password"], status=res["status"],
                         relay_jwt=res.get("jwt", ""))
        return 0 if res.get("status") == "registered" else 2
    res = asyncio.run(login(headless=a.headless, username=a.username,
                            password=a.password, timeout=a.timeout))
    if res.get("status") == "logged_in":
        acct = load_account()
        if acct.get("password"):
            print(f"  (password saved at {LEDGER})")
    return 0 if res.get("status") == "logged_in" else 2


if __name__ == "__main__":
    sys.exit(main())