#!/usr/bin/env python3
"""
scripts/github_bd_signup.py — GitHub signup through Bright Data's Scraping
Browser (Chromium over CDP), which solves DataDome. This is the path that works
when a plain IP is DataDome-blocked.

Flow: connect over CDP -> github.com/signup -> email/password/username ->
submit -> read the launch code from a Tempik mailbox -> verify -> save the
account to github_accounts.json.

Config comes from ~/.config/auto-freecf/.env:
  BRIGHTDATA_SCRAPING_BROWSER=wss://...@brd.superproxy.io:9222

Usage (camoufox venv — has playwright):
  camoufox-venv/bin/python scripts/github_bd_signup.py --dry-run   # reach the form, create nothing
  camoufox-venv/bin/python scripts/github_bd_signup.py
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
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

AUTO_FREECF = Path.home() / "Auto-FreeCF"
ACCOUNTS = AUTO_FREECF / "github_accounts.json"
EMAIL_INPUTS = ["#email", "input[name='email']"]
PASSWORD_INPUTS = ["#password", "input[name='password']"]
USERNAME_INPUTS = ["#login", "input[name='login']"]
SIGNUP_FORM = "form[action*='signup']"


def _env(key: str, default: str = "") -> str:
    v = os.environ.get(key, "")
    if v:
        return v
    try:
        for line in (Path.home() / ".config" / "auto-freecf" / ".env").read_text().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        pass
    return default


def _tinyfish_session(country: str = "US", api_key: str = "") -> str:
    """Create a TinyFish Browser API session; return its cdp_url (wss).

    TinyFish gives a remote Chromium over CDP with a STEALTH profile and a
    residential sticky exit IP (US by default) — the combo DataDome wants, and
    unlike Bright Data it does not forbid password typing.
    """
    import urllib.request
    key = api_key or _env("TINYFISH_API_KEY")
    if not key:
        raise RuntimeError("TINYFISH_API_KEY not set (agent.tinyfish.ai/api-keys)")
    body = json.dumps({"url": "https://github.com/signup",
                       "browser_profile": "stealth",
                       "proxy_config": {"enabled": True, "country_code": country}}).encode()
    req = urllib.request.Request("https://api.browser.tinyfish.ai", data=body, method="POST",
                                 headers={"X-API-Key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=90) as r:
        data = json.loads(r.read().decode() or "{}")
    cdp = data.get("cdp_url") or (data.get("data") or {}).get("cdp_url")
    if not cdp:
        raise RuntimeError(f"tinyfish: no cdp_url in response ({str(data)[:160]})")
    return cdp


def _username() -> str:
    return "kanca" + "".join(random.choices(string.ascii_lowercase + string.digits, k=7))


def _password() -> str:
    return "Gh" + random.choice("!@#$%^&*") + "".join(random.choices(string.ascii_letters + string.digits, k=12)) + "9a"


async def _first(page, selectors):
    for s in selectors:
        loc = page.locator(s).first
        if await loc.count():
            try:
                if await loc.is_visible():
                    return loc
            except Exception:
                return loc
    return None


async def _fill(page, loc, value: str) -> None:
    try:
        await loc.click(timeout=8000)
        await loc.fill(value)
    except Exception:
        await loc.evaluate("""(el, v) => { el.focus(); el.value = v;
            el.dispatchEvent(new Event('input', {bubbles:true}));
            el.dispatchEvent(new Event('change', {bubbles:true})); }""", value)
    await asyncio.sleep(0.6)


async def _wait_form(page, timeout=60) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if await page.locator(", ".join(EMAIL_INPUTS + ["#login"] + PASSWORD_INPUTS)).count():
            return True
        await asyncio.sleep(2)
    return False


async def run_bd(ws: str, *, dry_run: bool, headless: bool = False) -> dict:
    from playwright.async_api import async_playwright
    import mailboxes as M

    local = "gh" + "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
    mb = M.open_mailbox("auto", domain="kancalabs.my.id", local_part=local, log=print)
    email = mb.address
    password = _password()
    username = _username()
    print(f"  • Email    : {email}")
    print(f"  • Username : {username}")
    print(f"  • Password : {password}")

    async with async_playwright() as p:
        print("  connecting to the CDP cloud browser...")
        browser = None
        for _try in range(4):
            try:
                browser = await p.chromium.connect_over_cdp(ws, timeout=120000)
                break
            except Exception as exc:
                print(f"  connect try {_try+1} failed ({str(exc)[:70]}); retrying...")
                await asyncio.sleep(8)
        if browser is None:
            return {"status": "connect_failed"}
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        print("  navigating to github.com/signup ...")
        try:
            await page.goto("https://github.com/signup", wait_until="domcontentloaded", timeout=90000)
        except Exception as e:
            print(f"  ✗ nav error: {e}")
            await browser.close()
            return {"status": "nav_error", "error": str(e)}
        if not await _wait_form(page, 90):
            body = (await page.locator("body").inner_text())[:200]
            print(f"  ✗ signup form did not load (title={await page.title()!r} body={body!r})")
            await browser.close()
            return {"status": "no_form", "url": page.url}

        print("  ✓ signup form ready")
        if dry_run:
            print("  • dry-run: stopping before fill")
            await browser.close()
            return {"status": "dry_run", "url": page.url}

        e = await _first(page, EMAIL_INPUTS)
        await _fill(page, e, email)
        pw = await _first(page, PASSWORD_INPUTS)
        await _fill(page, pw, password)
        await asyncio.sleep(1.0)
        un = await _first(page, USERNAME_INPUTS)
        if un:
            await _fill(page, un, username)
            await asyncio.sleep(3)

        # submit (scope to the signup form, never the OAuth buttons)
        btn = page.locator(f"{SIGNUP_FORM} button[type='submit']").first
        if await btn.count() == 0:
            btn = page.locator("button[type='submit']").first
        try:
            await btn.click(timeout=8000)
        except Exception:
            await page.evaluate("""() => { const b=document.querySelector("form[action*='signup'] button[type=submit]"); if(b) b.click(); }""")
        print("  → submitted; waiting for verification or dashboard...")
        await asyncio.sleep(6)

        # maybe a launch-code page
        for _ in range(20):
            if await page.locator("input[id^='launch-code-'], input#otp, input[name='otp'], input[autocomplete='one-time-code']").count():
                break
            if "signup" not in (page.url or ""):
                break
            await asyncio.sleep(2)

        if await page.locator("input[id^='launch-code-'], input#otp, input[name='otp'], input[autocomplete='one-time-code']").count():
            print("  → launch-code page; reading code from mailbox...")
            code = await asyncio.to_thread(mb.code, "github", 180.0, 5.0)
            if not code:
                print("  ✗ no launch code arrived")
                await browser.close()
                return {"status": "no_code", "email": email, "password": password, "username": username}
            print(f"  ✓ launch code: {code}")
            boxes = await page.locator("input[id^='launch-code-']").count()
            if boxes >= len(code):
                for i, d in enumerate(code):
                    await page.locator("input[id^='launch-code-']").nth(i).fill(d)
                    await asyncio.sleep(0.1)
            else:
                box = await _first(page, ["input#otp", "input[name='otp']", "input[autocomplete='one-time-code']"])
                if box:
                    await box.fill(code)
            # submit the code
            for label in ("Create account", "Continue", "Verify", "Submit"):
                b = page.get_by_role("button", name=label).first
                if await b.count() and await b.is_visible():
                    await b.click()
                    break
            await asyncio.sleep(6)

        ok = False
        for c in await ctx.cookies():
            if c.get("name") == "logged_in" and str(c.get("value")).lower() == "yes":
                ok = True
        if not ok:
            # fall back: off /signup without verify markers
            ok = "signup" not in (page.url or "")
        print(f"  url now: {page.url} | logged_in={ok}")
        await browser.close()

    if not ok:
        return {"status": "not_verified", "email": email, "password": password, "username": username, "url": page.url}

    _save_account(email, password, username)
    print("  ✅ GitHub account created and saved to github_accounts.json")
    return {"status": "created", "email": email, "password": password, "username": username}


def _save_account(email: str, password: str, username: str) -> None:
    try:
        data = json.loads(ACCOUNTS.read_text()) if ACCOUNTS.exists() else {"accounts": []}
    except Exception:
        data = {"accounts": []}
    if isinstance(data, list):
        data = {"accounts": data}
    data.setdefault("accounts", []).append({
        "username": username, "password": password, "email": email,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "brightdata-cdp",
    })
    data["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    ACCOUNTS.write_text(json.dumps(data, indent=2))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="GitHub signup via a CDP cloud browser (TinyFish or Bright Data)")
    ap.add_argument("--dry-run", action="store_true", help="reach the form, create nothing")
    ap.add_argument("--ws", default=None, help="override the CDP wss:// endpoint")
    ap.add_argument("--tinyfish", action="store_true",
                    help="create a TinyFish Browser session (stealth + residential); needs TINYFISH_API_KEY")
    ap.add_argument("--tinyfish-country", default="US", help="TinyFish residential country (default US)")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.tinyfish:
        try:
            ws = _tinyfish_session(a.tinyfish_country)
            print(f"  tinyfish session cdp_url: {ws[:60]}...")
        except Exception as e:
            print(f"  ✗ TinyFish session failed: {e}")
            return 1
    else:
        ws = a.ws or _env("BRIGHTDATA_SCRAPING_BROWSER")
    if not ws:
        print("  ✗ no endpoint: pass --tinyfish, --ws, or set BRIGHTDATA_SCRAPING_BROWSER")
        return 1
    res = asyncio.run(run_bd(ws, dry_run=a.dry_run))
    print("  result:", res.get("status"))
    return 0 if res.get("status") in ("created", "dry_run") else 2


if __name__ == "__main__":
    sys.exit(main())