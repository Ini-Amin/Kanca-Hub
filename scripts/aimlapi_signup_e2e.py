#!/usr/bin/env python3
"""aimlapi_signup_e2e.py — ONE gentle signup attempt on aimlapi.com with a Tempik mailbox.

Recon-grade by design (the form is client-rendered, so fields are unknown):
  1. allocate Tempik mailbox + persona password
  2. open signup page, dump EVERY visible input to evidence, click "Sign Up" tab if there
  3. fill email/password by best-fit selector, submit exactly once
  4. watch DOM + network, poll Tempik for verification mail (code or link)
  5. write an account jsonl + screenshots + DOM dumps  (no /tmp — durable scratch dir)

Usage:  python3 scripts/aimlapi_signup_e2e.py [--headed]
"""
from __future__ import annotations

import argparse, asyncio, json, re, sys, time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
from camoufox.async_api import AsyncCamoufox
import mailboxes as M
import persona

CONFIG_DIR = Path.home() / ".config" / "auto-freecf"
SCRATCH = CONFIG_DIR / "aimlapi_scratch"
ACCOUNTS = CONFIG_DIR / "aimlapi_accounts.jsonl"
URL = "https://aimlapi.com/app/sign-up/"


async def dump_form(page, tag: str) -> list[dict]:
    info = []
    for inp in await page.query_selector_all("input, textarea, button[type=submit]"):
        try:
            info.append({k: await inp.get_attribute(k)
                         for k in ("name", "id", "type", "placeholder", "autocomplete", "required")})
        except Exception:
            pass
    (SCRATCH / f"fields_{tag}.json").write_text(json.dumps(info, indent=2))
    return info


async def _code_input_fill(page, code: str) -> bool:
    """Enter a 6-digit code into either one input or six boxes."""
    boxes = await page.query_selector_all("input[maxlength='1'], input[autocomplete='one-time-code']")
    if len(boxes) >= 6:
        for i, ch in enumerate(code[:6]):
            await boxes[i].fill(ch)
            await asyncio.sleep(0.15)
        return True
    one = await page.query_selector("input[placeholder*='code' i], input[name*='code' i], "
                                    "input[autocomplete='one-time-code'], input[inputmode='numeric']")
    if one:
        await one.fill(code)
        return True
    return False


async def _goto(page, url: str) -> None:
    """goto that survives the app's own client-side redirect racing us."""
    for attempt in (1, 2):
        try:
            await page.goto(url, wait_until="load", timeout=45000)
            await asyncio.sleep(3)
            return
        except Exception as e:
            if attempt == 2:
                print(f"        goto failed twice: {str(e)[:80]} — staying at {page.url}")
                return
            await asyncio.sleep(3)


async def _login_if_needed(page, email: str, password: str) -> None:
    """If bounced to the auth page, log in with the credentials we saved."""
    if "/auth" not in page.url and "log-in" not in page.url.lower():
        return
    print("        bounced to auth — logging in…")
    for _ in range(4):                      # SPA render takes a beat
        em = await page.query_selector("input[type=email], input[name*=email i]")
        if em:
            break
        tab = await page.query_selector("text=Log In")   # form hides behind a tab
        if tab:
            try:
                await tab.click()
                await asyncio.sleep(2)
            except Exception:
                pass
        await asyncio.sleep(2.5)
    pw_ = await page.query_selector("input[type=password]")
    if em and pw_:
        await em.fill(email)
        await asyncio.sleep(0.3)
        await pw_.fill(password)
        await asyncio.sleep(0.3)
        btn = await page.query_selector("button[type=submit], button:has-text('Log in'), "
                                        "button:has-text('Log In')")
        await (btn.click() if btn else page.keyboard.press("Enter"))
        await asyncio.sleep(6)


async def verify_account(email: str, password: str, code: str, headless: bool = True) -> None:
    """Finish activation for an account that already did signup+OTP-receive."""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    async with AsyncCamoufox(headless=headless, humanize=True, os="windows") as browser:
        page = await browser.new_page()
        await page.goto("https://aimlapi.com/app/", wait_until="load", timeout=45000)
        await asyncio.sleep(5)
        print("        url now:", page.url)
        await _login_if_needed(page, email, password)
        await asyncio.sleep(4)
        await _goto(page, f"https://aimlapi.com/app/verify-email?from={email}&returnTo=/sign-up/")
        await dump_form(page, "verify")
        ok = await _code_input_fill(page, code)
        print(f"[•/3] code inputs filled: {ok}")
        btn = await page.query_selector("button[type=submit], button:has-text('Verify'), "
                                        "button:has-text('Continue')")
        if btn and await btn.is_visible():
            await btn.click()
        else:
            await page.keyboard.press("Enter")
        await asyncio.sleep(7)
        await page.screenshot(path=str(SCRATCH / "04_after_verify.png"))
        (SCRATCH / "dom_verify.txt").write_text((await page.inner_text("body"))[:6000])
        print("        final url:", page.url)
        cookies = await page.context.cookies()
        tokens = {c["name"]: c["value"] for c in cookies
                  if ("token" in c["name"].lower() or "session" in c["name"].lower())}
        body = (await page.inner_text("body")).lower()
        verified = any(k in body for k in ("welcome", "dashboard", "credits", "you're in"))
        with open(ACCOUNTS, "a") as f:
            f.write(json.dumps({"ts": int(time.time()), "email": email, "phase": "verified",
                                "verified": verified, "tokens": list(tokens)}) + "\n")
        print(f"[✓] verified={verified}  tokens={list(tokens)}")


async def peek_balance(email: str, password: str, headless: bool = True) -> None:
    """Logged-in peek: what does the dashboard say about credits/quota?"""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    async with AsyncCamoufox(headless=headless, humanize=True, os="windows") as browser:
        page = await browser.new_page()
        await _goto(page, "https://aimlapi.com/app/")
        print("        url now:", page.url)
        await _login_if_needed(page, email, password)
        await asyncio.sleep(5)
        await page.screenshot(path=str(SCRATCH / "05_dashboard.png"))
        body = await page.inner_text("body")
        (SCRATCH / "dom_dashboard.txt").write_text(body[:8000])
        print("-------- money lines --------")
        for line in body.splitlines():
            l = line.strip()
            if re.search(r"(?i)(credit|balance|\$|plan|quota|limit|free|trial|usage)",
                         l) and len(l) < 140:
                print("  ", l)
        print("-----------------------------")
        print("        final url:", page.url)


async def main(headless: bool) -> int:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    box = M.open_mailbox("tempik", domain="kancalabs.my.id")
    email, password = box.address, persona.password()
    print(f"[1/5] mailbox : {email}")

    api_events: list[dict] = []
    async with AsyncCamoufox(headless=headless, humanize=True, os="windows") as browser:
        page = await browser.new_page()

        async def on_response(resp):
            if resp.request.method in ("POST", "PUT") and any(
                    s in resp.url for s in ("/api/", "auth", "sign", "register", "identity", "key")):
                try:
                    api_events.append({"url": resp.url, "status": resp.status,
                                       "body": (await resp.text())[:1200]})
                except Exception:
                    pass
        page.on("response", on_response)

        print("[2/5] landing…")
        await page.goto(URL, wait_until="load", timeout=45000)
        await asyncio.sleep(5)
        await page.screenshot(path=str(SCRATCH / "01_landing.png"))
        tab = await page.query_selector("text=Sign Up")
        if tab:
            try:
                await tab.click()
                await asyncio.sleep(2.5)
            except Exception:
                pass
        await page.screenshot(path=str(SCRATCH / "02_tab.png"))
        fields = await dump_form(page, "signup")
        print("        fields:", json.dumps(fields)[:900])

        filled = {}
        for sel, val, key in (("input[type=email], [autocomplete=email], [name*=email i]",
                               email, "email"),
                              ("input[type=password]", password, "password")):
            el = await page.query_selector(sel)
            if el:
                try:
                    await el.fill(val, timeout=5000)
                    filled[key] = True
                    await asyncio.sleep(0.4)
                except Exception:
                    pass
        print(f"[3/5] filled: {filled}")
        if not filled.get("email"):
            (SCRATCH / "dom.txt").write_text((await page.inner_text("body"))[:6000])
            print("!! no email field visible — stopped BEFORE submit; evidence in", SCRATCH)
            return 1

        btn = None
        for sel in ("button:has-text('Sign up')", "button:has-text('Sign Up')",
                    "button:has-text('Create')", "button[type=submit]"):
            btn = await page.query_selector(sel)
            if btn and await btn.is_visible():
                break
        if btn:
            await btn.click()
        else:
            await page.keyboard.press("Enter")
        await asyncio.sleep(6)
        await page.screenshot(path=str(SCRATCH / "03_after_submit.png"))
        (SCRATCH / "dom_after.txt").write_text((await page.inner_text("body"))[:6000])
        print(f"        url now: {page.url}")
        for line in (await page.inner_text("body")).splitlines():
            l = line.strip().lower()
            if any(k in l for k in ("verify", "code", "sent", "check", "invalid",
                                    "error", "captcha", "confirm")):
                print("    [DOM]", line.strip()[:120])

        print("[4/5] polling Tempik (≤120 s)…")
        otp, link, got_mail = None, None, False
        endt = time.time() + 120
        while time.time() < endt:
            for m in box.read():
                got_mail = True
                blob = " ".join(str(m.get(k) or "") for k in
                                ("subject", "text", "body", "html", "source"))
                plain = re.sub(r"<[^>]+>", " ", blob)
                m1 = re.search(r"(?i)\b(\d{6})\b", plain)
                if m1:
                    otp = m1.group(1)
                m2 = re.search(r"https?://[^\s\"'>]+(?:verify|confirm|activ)[^\s\"'>]*", blob)
                if m2:
                    link = m2.group(0)
                    (SCRATCH / "verify_link.txt").write_text(link)
            if otp or link:
                break
            await asyncio.sleep(4)
        print(f"        mail={got_mail}  code={otp or '-'}  link={bool(link)}")

        # OTP entry + onboarding key capture — all in this one session
        api_key = None
        if otp:
            print("[4b] entering code…")
            await _code_input_fill(page, otp)
            btn = await page.query_selector("button[type=submit], button:has-text('Verify'), "
                                            "button:has-text('Continue')")
            await (btn.click() if btn else page.keyboard.press("Enter"))
            endt = time.time() + 30
            while time.time() < endt and "onboarding" not in page.url:
                await asyncio.sleep(2)
            print("        url now:", page.url)
            await asyncio.sleep(5)
            for ev in api_events:                   # "full value once" = it flies by here
                m3 = re.search(r"[\"'](?:api_?key|key)[\"']\s*[:=]\s*[\"']([A-Za-z0-9\-_\.]{20,})[\"']",
                               ev.get("body", ""))
                if m3:
                    api_key = m3.group(1)
                    break
            print(f"        api_key: {'captured: ' + api_key[:8] + '…' if api_key else 'NOT in sniffed payloads'}")
            await page.screenshot(path=str(SCRATCH / "06_farm_one.png"))

    print("[5/5] recording…")
    ACCOUNTS.parent.mkdir(parents=True, exist_ok=True)
    with open(ACCOUNTS, "a") as f:
        f.write(json.dumps({"ts": int(time.time()), "email": email, "password": password,
                            "mail_arrived": got_mail, "otp": otp, "api_key": api_key,
                            "api_events": api_events}) + "\n")
    print("saved ->", ACCOUNTS)
    return 0 if (got_mail or api_events) else 2


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--login-verify", nargs=3, metavar=("EMAIL", "PASS", "CODE"),
                    help="finish activation for an already-pending account")
    ap.add_argument("--peek", nargs=2, metavar=("EMAIL", "PASS"),
                    help="logged-in dashboard peek: credits/quota facts")
    a = ap.parse_args()
    if a.login_verify:
        email, password, code = a.login_verify
        asyncio.run(verify_account(email, password, code, headless=not a.headed))
    elif a.peek:
        email, password = a.peek
        asyncio.run(peek_balance(email, password, headless=not a.headed))
    else:
        asyncio.run(main(headless=not a.headed))
