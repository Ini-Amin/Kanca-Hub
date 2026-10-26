#!/usr/bin/env python3
"""qoder_signup_e2e.py — Clean browser-driven registration for Qoder using domain email.

Uses Camoufox (humanized anti-detect browser) to register a new account on
https://qoder.com/users/sign-up with a disposable domain email (kancalabs.my.id via Tempik).
Solves the Aliyun Captcha via captcha_slider.solve_aliyun(), fetches the 6-digit OTP
from the mailbox, and completes registration cleanly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

# Add repo scripts to sys.path
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from camoufox.async_api import AsyncCamoufox
import captcha_slider
import mailboxes as M

CONFIG_DIR = Path.home() / ".config" / "auto-freecf"
QODER_ACCOUNTS_FILE = CONFIG_DIR / "qoder_accounts.jsonl"


def generate_secure_password(length: int = 16) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def run_qoder_signup(
    email: str | None = None,
    password: str | None = None,
    first_name: str = "Ame",
    last_name: str = "Kanca",
    headless: bool = True,
):
    print("=== Qoder Clean Browser Sign-Up (Domain Email) ===")

    # 1. Setup Mailbox
    box = None
    if not email:
        print("[1/6] Allocating temporary mailbox on kancalabs.my.id (Tempik)...")
        box = M.open_mailbox("tempik", domain="kancalabs.my.id")
        email = box.address
    else:
        print(f"[1/6] Using provided email: {email}")

    password = password or generate_secure_password()
    print(f"      Email   : {email}")
    print(f"      Password: {password}")

    # 2. Launch Browser
    print("[2/6] Launching Camoufox (humanize=True, OS=windows)...")
    async with AsyncCamoufox(headless=headless, humanize=True, os="windows") as browser:
        page = await browser.new_page()

        # Capture key API network responses
        registered_success = False

        async def on_response(resp):
            nonlocal registered_success
            url = resp.url
            if "/api/v1/users" in url and resp.request.method == "POST":
                try:
                    txt = await resp.text()
                    print(f"\n[+] Users API response: [{resp.status}] {txt[:200]}")
                    if resp.status in (200, 201):
                        registered_success = True
                except Exception:
                    pass

        page.on("response", on_response)

        # 3. Load Sign-up Page
        print("[3/6] Navigating to https://qoder.com/users/sign-up...")
        await page.goto("https://qoder.com/users/sign-up", wait_until="load", timeout=35000)
        await asyncio.sleep(4)

        # Fill Step 1
        fn_input = await page.wait_for_selector("#basic_firstName", timeout=12000)
        ln_input = await page.wait_for_selector("#basic_lastName", timeout=12000)
        em_input = await page.wait_for_selector("#basic_email", timeout=12000)
        cb_input = await page.wait_for_selector("input[type='checkbox']", timeout=12000)

        print("[4/6] Filling First Name, Last Name, Email, and agreeing to Terms...")
        await fn_input.fill(first_name)
        await asyncio.sleep(0.4)
        await ln_input.fill(last_name)
        await asyncio.sleep(0.4)
        await em_input.fill(email)
        await asyncio.sleep(0.4)
        await cb_input.check()
        await asyncio.sleep(0.4)

        btn = await page.wait_for_selector("button:has-text('Continue')")
        await btn.click()
        await asyncio.sleep(2.5)

        # Fill Step 2: Password
        pw_input = await page.wait_for_selector("#basic_password", timeout=12000)
        print("      Filling password...")
        await pw_input.fill(password)
        await asyncio.sleep(0.5)

        btn = await page.wait_for_selector("button:has-text('Continue')")
        await btn.click()
        await asyncio.sleep(3.0)

        # Step 3: Aliyun Captcha
        print("[5/6] Solving Aliyun Captcha widget via captcha_slider...")
        c_body = await page.query_selector("#aliyunCaptcha-captcha-body, #aliyunCaptcha-captcha-wrapper, #captcha-element")
        if c_body and await c_body.is_visible():
            print("      Clicking Aliyun captcha box to trigger slider...")
            await c_body.click()
            await asyncio.sleep(2.0)

        solved = await captcha_slider.solve_aliyun(page, puzzles=20)
        print(f"      Captcha solve result: {solved}")
        await asyncio.sleep(4)
        await page.screenshot(path="/tmp/qoder_probe/after_solve.png")
        print(f"      Page URL after solve: {page.url}")

        # Check for error or success notifications
        body_text = await page.inner_text("body")
        for line in body_text.splitlines():
            l = line.strip()
            if any(k in l.lower() for k in ["code", "verify", "email", "sent", "check", "error", "success", "welcome"]):
                print(f"      [DOM Text] {l}")
        await asyncio.sleep(0.5)
        print("[6/6] Checking for verification step...")
        await asyncio.sleep(3)

        # Poll Tempik for Qoder OTP
        otp = None
        if box:
            print("      Polling Tempik mailbox for Qoder verification code...")
            end_time = time.time() + 90.0
            while time.time() < end_time and not otp:
                for m in box.read():
                    body = m.get("body") or m.get("text") or ""
                    plain = re.sub(r"<[^>]+>", " ", body)
                    match = (
                        re.search(r"start using qoder\.\s*(\d{6})", plain, re.I)
                        or re.search(r"\b(\d{6})\b\s*this code expires", plain, re.I)
                        or re.search(r"verify your email.*?(\d{6})", plain, re.I)
                    )
                    if match:
                        otp = match.group(1)
                        break
                if not otp:
                    await asyncio.sleep(3.0)

        if otp:
            print(f"[+] Successfully extracted OTP: {otp}")
            # Check for Ant Design OTP inputs or generic input
            otp_inputs = await page.query_selector_all(".ant-otp input, .ant-otp-input, input[autocomplete='one-time-code']")
            if len(otp_inputs) >= 6:
                print("      Entering 6 digits into Antd OTP boxes...")
                for idx_d, digit in enumerate(otp[:6]):
                    await otp_inputs[idx_d].fill(digit)
                    await asyncio.sleep(0.15)
            else:
                generic_inp = await page.query_selector("input[placeholder*='code' i], input[placeholder*='verification' i], input[name='code'], #basic_code, input[type='text']")
                if generic_inp:
                    print("      Filling generic OTP input...")
                    await generic_inp.fill(otp)

            await asyncio.sleep(1.0)
            submit_btn = await page.query_selector("button[type='submit'], button:has-text('Verify'), button:has-text('Continue'), button:has-text('Sign up')")
            if submit_btn and await submit_btn.is_visible():
                print("      Clicking verification submit button...")
                await submit_btn.click()
            else:
                await page.keyboard.press("Enter")
            await asyncio.sleep(6.0)
        else:
            print("[-] No OTP code received or manual OTP entry required.")
        # Save cookies / session state
        cookies = await page.context.cookies()
        session_tokens = {c["name"]: c["value"] for c in cookies if "token" in c["name"].lower() or "session" in c["name"].lower()}
        print(f"[+] Cookies captured: {len(cookies)} (Session tokens: {list(session_tokens.keys())})")

        # Save to accounts file
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": int(time.time()),
            "email": email,
            "password": password,
            "tokens": session_tokens,
            "registered": registered_success or bool(session_tokens),
        }
        with open(QODER_ACCOUNTS_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")

        print(f"\n========================================================")
        print(f" [SUCCESS] Qoder Account Recorded!")
        print(f" - Email   : {email}")
        print(f" - Password: {password}")
        print(f" - Saved To: {QODER_ACCOUNTS_FILE}")
        print(f"========================================================")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clean Browser Qoder Sign-Up")
    parser.add_argument("--email", type=str, default="", help="Custom domain email (optional)")
    parser.add_argument("--password", type=str, default="", help="Custom password (optional)")
    parser.add_argument("--headed", action="store_true", help="Run headed/visible browser")
    args = parser.parse_args()

    asyncio.run(
        run_qoder_signup(
            email=args.email or None,
            password=args.password or None,
            headless=not args.headed,
        )
    )
