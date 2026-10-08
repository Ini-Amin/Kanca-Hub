#!/usr/bin/env python3
"""
scripts/google_signup_cdp.py — create a Google account through a CDP cloud
browser (TinyFish by default: stealth + residential), driving the real signup
flow. Falls back to a Litensi SMS number for the phone gate if needed.

Steps driven (Google's lifecycle signup SPA):
  name -> birthday+gender (custom comboboxes) -> username -> password
  -> phone (if forced; solved via Litensi SMS) -> terms

Usage (camoufox venv):
  camoufox-venv/bin/python scripts/google_signup_cdp.py --dry-run   # reach the form, stop
  camoufox-venv/bin/python scripts/google_signup_cdp.py             # full attempt
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import string
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

OUT = Path.home() / ".local/share/auto-freecf/google_accounts.json"
SIGNUP = ("https://accounts.google.com/signup/v2/webcreateaccount"
          "?flowName=GlifWebSignIn&flowEntry=SignUp&hl=en")


def _nm() -> tuple[str, str, str]:
    first = random.choice(["Kevin", "Bryan", "Andre", "Rizky", "Damar"])
    last = random.choice(["Rahardjo", "Santoso", "Pratama", "Halim", "Wijaya"])
    uname = (first + last).lower() + str(random.randint(10, 9999))
    return first, last, uname


def _pw() -> str:
    return "Kv" + "".join(random.choices(string.ascii_letters + string.digits, k=10)) + "!7a"


async def _click_next(page) -> bool:
    for label in ("Next", "Continue"):
        b = page.get_by_role("button", name=label, exact=True)
        if await b.count():
            try:
                await b.first.click(timeout=8000)
                return True
            except Exception:
                pass
    return False


async def _pick_combobox(page, label: str, option_text: str) -> bool:
    """Click a Google MD combobox and choose an option.

    Google's birthday/gender comboboxes have NO aria-label; they are identified
    by their visible placeholder text ("Month" / "Gender"). Match on text.
    """
    cb = page.locator(f"div[role='combobox']").filter(has_text=label).first
    if await cb.count() == 0:
        cb = page.locator(f"[role='combobox'][aria-label='{label}']").first
    try:
        await cb.click(timeout=8000)
        await asyncio.sleep(1.2)
        # exact text match — "Male" must not match "Female"
        opt = page.locator("ul[role='listbox'] li[role='option']", has_text=option_text)
        n = await opt.count()
        chosen = None
        for i in range(n):
            t = (await opt.nth(i).inner_text()).strip()
            if t == option_text:
                chosen = opt.nth(i)
                break
        if chosen is None and n:
            chosen = opt.first
        if chosen is not None:
            await chosen.click(timeout=8000)
            return True
    except Exception:
        pass
    return False


async def _solve_phone_gate(page, first: str, last: str, uname: str, password: str) -> dict:
    """At Google's phone gate: order a Litensi number, enter it, read the SMS
    from the webhook (sms_latest.json), enter the code, and continue."""
    import subprocess, json as _json
    from pathlib import Path as _P
    latest = _P.home() / ".config" / "auto-freecf" / "sms_latest.json"
    scripts = _P(__file__).resolve().parent
    # order a number (browser-driven; Litensi has no REST for phone activations)
    print("  → ordering a Litensi number …")
    try:
        out = subprocess.run([sys.executable, str(scripts / "otp_litensi_sms.py"),
                              "--country", "Indonesia", "--service", "Google,youtube,Gmail"],
                             capture_output=True, text=True, timeout=300)
    except Exception as e:
        return {"status": "phone_order_failed", "error": str(e)[:120]}
    import re as _re
    m = _re.search(r"LITENSI NUMBER:\s*(\d+)", out.stdout) or _re.search(r"'number': '(\d+)'", out.stdout) \
        or _re.search(r"\b(62\d{9,13})\b", out.stdout)
    if not m:
        return {"status": "phone_order_failed", "stdout": out.stdout[-300:]}
    number = m.group(1)
    print(f"  → number {number}; entering it")
    tel = page.locator("input[type='tel'], input[name='phoneNumber'], input#phoneNumberId").first
    try:
        if await tel.count():
            await tel.fill(number)
        else:
            await page.evaluate("""(n)=>{const i=document.querySelector("input[type='tel'],input#phoneNumberId");
                if(i){i.focus();i.value=n;i.dispatchEvent(new Event('input',{bubbles:true}));}}""", number)
    except Exception:
        pass
    await asyncio.sleep(1)
    await _click_next(page)
    await asyncio.sleep(6)
    # wait for the SMS code via the webhook
    print("  → waiting for SMS via webhook …")
    code = None
    if latest.exists():
        try:
            latest.unlink()  # only accept a NEW code
        except Exception:
            pass
    end = time.time() + 240
    while time.time() < end and not code:
        if latest.exists():
            try:
                code = _json.loads(latest.read_text()).get("code")
            except Exception:
                code = None
        if not code:
            await asyncio.sleep(4)
    if not code:
        return {"status": "no_sms", "number": number}
    print(f"  → SMS code {code}; entering it")
    gc = page.locator("input[name='code'], input#code").first
    try:
        if await gc.count():
            await gc.fill(code)
        else:
            await page.evaluate("""(c)=>{const i=document.querySelector("input[name='code'],input#code");
                if(i){i.focus();i.value=c;i.dispatchEvent(new Event('input',{bubbles:true}));}}""", code)
    except Exception:
        pass
    await asyncio.sleep(1)
    await _click_next(page)
    await asyncio.sleep(8)
    url = page.url
    ok = "signup" not in url or "mophoneverification" not in url
    print(f"  → after SMS code: {url[:70]}  ok={ok}")
    return {"status": "created" if ok else "sms_rejected", "url": url,
            "email": f"{uname}@gmail.com", "password": password, "number": number}


async def run(ws: str, *, dry_run: bool, sms_webhook: bool = False) -> dict:
    from playwright.async_api import async_playwright
    first, last, uname = _nm()
    password = _pw()
    print(f"  identity: {first} {last} | {uname}@gmail.com")

    async with async_playwright() as p:
        browser = None
        for t in range(3):
            try:
                browser = await p.chromium.connect_over_cdp(ws, timeout=120000)
                break
            except Exception as e:
                print(f"  connect {t+1} failed ({str(e)[:60]}); retry")
                await asyncio.sleep(6)
        if browser is None:
            return {"status": "connect_failed"}
        ctx = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(SIGNUP, wait_until="domcontentloaded", timeout=90000)
        for _ in range(20):
            await asyncio.sleep(3)
            if await page.locator("input[name='firstName']").count():
                break
        if await page.locator("input[name='firstName']").count() == 0:
            body = ""
            try: body = await page.evaluate("() => (document.body&&document.body.innerText||'').slice(0,150)")
            except Exception: pass
            print(f"  ✗ name form not reached: {page.url[:70]} | {body!r}")
            await browser.close()
            return {"status": "no_form", "url": page.url}
        print("  ✓ name step reached")
        if dry_run:
            await browser.close()
            return {"status": "dry_run", "url": page.url}

        # STEP 1: name
        await page.locator("input[name='firstName']").fill(first)
        await page.locator("input[name='lastName']").fill(last)
        await asyncio.sleep(0.6)
        await _click_next(page)
        await asyncio.sleep(5)

        # STEP 2: birthday + gender
        if "birthdaygender" in page.url or await page.locator("input[name='day']").count():
            try:
                await page.locator("input[name='day']").fill(str(random.randint(2, 27)))
                await page.locator("input[name='year']").fill(str(random.randint(1985, 1999)))
            except Exception as e:
                print(f"  day/year: {str(e)[:60]}")
            await _pick_combobox(page, "Month", random.choice(
                ["January", "March", "May", "July", "September", "November"]))
            await _pick_combobox(page, "Gender", "Male")
            await asyncio.sleep(0.8)
            await _click_next(page)
            await asyncio.sleep(5)
            print(f"  → after birthday: {page.url[:70]}")

        # STEP 3: username
        if "username" in page.url or await page.locator("input[name='Username']").count():
            try:
                u = page.locator("input[name='Username'], input#username").first
                await u.fill(uname)
                await asyncio.sleep(0.6)
                await _click_next(page)
                await asyncio.sleep(5)
                print(f"  → after username: {page.url[:70]}")
            except Exception as e:
                print(f"  username: {str(e)[:60]}")

        # STEP 4: password
        if "password" in page.url or await page.locator("input[name='Passwd']").count():
            try:
                # Google shows TWO password inputs (Password + Confirm); fill both.
                n = await page.locator("input[type='password']").count()
                for i in range(max(1, n)):
                    try:
                        await page.locator("input[type='password']").nth(i).fill(password)
                        await asyncio.sleep(0.3)
                    except Exception:
                        pass
                await _click_next(page)
                await asyncio.sleep(6)
                print(f"  → after password: {page.url[:70]}")
            except Exception as e:
                print(f"  password: {str(e)[:60]}")

        # Where did we land?
        txt = ""
        try: txt = await page.evaluate("() => (document.body&&document.body.innerText||'').slice(0,240)")
        except Exception: pass
        phone = ("phone" in page.url.lower()) or ("phone" in txt.lower()) or \
            await page.locator("input[type='tel'], input[name='phoneNumber']").count() > 0
        print(f"  final url: {page.url[:80]}")
        print(f"  page text: {txt[:200]!r}")
        print(f"  PHONE GATE: {phone}")
        if phone and sms_webhook:
            res = await _solve_phone_gate(page, first, last, uname, password)
            await browser.close()
            return res
        await browser.close()
        return {"status": "phone_gate" if phone else "reached",
                "url": page.url, "email": f"{uname}@gmail.com", "password": password}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Create a Google account via a CDP cloud browser")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ws", default=None, help="CDP wss:// endpoint (default: TinyFish session)")
    ap.add_argument("--country", default="US", help="TinyFish residential country")
    ap.add_argument("--sms-webhook", action="store_true",
                    help="on the phone gate, order a Litensi number and read the SMS via the webhook")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    ws = a.ws
    if not ws:
        import github_bd_signup as G
        ws = G._tinyfish_session(a.country)
        print(f"  tinyfish session: {ws[:50]}...")
    res = asyncio.run(run(ws, dry_run=a.dry_run, sms_webhook=a.sms_webhook))
    print("  result:", json.dumps(res)[:200])
    return 0 if res.get("status") in ("dry_run", "reached", "phone_gate", "created") else 2


if __name__ == "__main__":
    sys.exit(main())