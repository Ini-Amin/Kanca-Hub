#!/usr/bin/env python3
"""End-to-End Automated Mozilla FxA + Tempik Registration & Fastly 50 GB Activation.

Uses Camoufox (headless Firefox engine) and Tempik (kancalabs.my.id disposable mail)
to register or log in to Firefox Accounts, automatically grab the 6-digit email OTP,
acquire the OAuth VPN token, and mint the 50 GB Fastly ProxyPass JWT.

Usage:
  # Fully automatic (new disposable mailbox on kancalabs.my.id):
  /home/amen/.local/share/auto-freecf/venv/bin/python scripts/fxa_tempik_e2e.py

  # Using an existing domain email:
  /home/amen/.local/share/auto-freecf/venv/bin/python scripts/fxa_tempik_e2e.py --email user@kancalabs.my.id --password 'MyPass123!'
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import secrets
import sys
import time
from pathlib import Path

# Local repo imports
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import fxa_login_cli
import mailboxes
import mozilla_guardian_client

try:
    from camoufox.async_api import AsyncCamoufox
except ImportError:
    # Try local venv
    sys.exit(
        "Camoufox not found in current environment.\n"
        "Run with: /home/amen/.local/share/auto-freecf/venv/bin/python " + __file__
    )


def generate_secure_password(length: int = 16) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def run_fxa_automation(
    email: str | None = None,
    password: str | None = None,
    headless: bool = True,
):
    # 1. Setup Mailbox
    mb = None
    if not email:
        print("[1/5] Creating temporary mailbox on kancalabs.my.id...")
        mb = mailboxes.open_mailbox("tempik", domain="kancalabs.my.id")
        email = mb.address
        password = password or generate_secure_password()
        print(f"      Assigned: {email}")
        print(f"      Password: {password}")
    else:
        print(f"[1/5] Using provided account: {email}")
        if not password:
            password = generate_secure_password()

    # 2. PKCE Setup
    print("[2/5] Initializing OAuth 2.0 PKCE challenge...")
    verifier, challenge = fxa_login_cli.generate_pkce()
    state = fxa_login_cli.base64url_encode(secrets.token_bytes(16))
    auth_url = fxa_login_cli.get_auth_url(challenge, state)

    auth_code = None

    # 3. Browser Automation via Camoufox
    print("[3/5] Launching Camoufox automation...")
    async with AsyncCamoufox(headless=headless) as browser:
        page = await browser.new_page()

        # Listen for the redirect containing the OAuth code
        def on_request(req):
            nonlocal auth_code
            if "android-redirect.html" in req.url and "code=" in req.url:
                m = re.search(r"[?&]code=([a-zA-Z0-9_-]+)", req.url)
                if m:
                    auth_code = m.group(1)
                    print(f"\n[+] Intercepted OAuth code: {auth_code[:12]}...")

        page.on("request", on_request)

        print(f"      Navigating to FxA authorization...")
        await page.goto(auth_url, wait_until="networkidle", timeout=30000)

        # Fill Email
        email_input = await page.wait_for_selector('input[type="email"], input[name="email"]', timeout=15000)
        await email_input.fill(email)
        await page.keyboard.press("Enter")
        await asyncio.sleep(2)

        # Check if password field appears (login or signup)
        pwd_input = await page.wait_for_selector('input[type="password"]', timeout=15000)
        await pwd_input.fill(password)

        # If age or repeat password fields appear (registration form)
        repeat_pwd = await page.query_selector('input[name="repeat_password"]')
        if repeat_pwd:
            await repeat_pwd.fill(password)

        age_input = await page.query_selector('input[name="age"], input[type="number"]')
        if age_input:
            await age_input.fill("25")

        # Submit login / signup form
        submit_btn = await page.query_selector('button[type="submit"]')
        if submit_btn:
            await submit_btn.click()
        else:
            await page.keyboard.press("Enter")

        print("      Submitted credentials, awaiting verification step...")

        # 4. Check for OTP Email if needed
        otp_needed = False
        try:
            # Wait for 6-digit code input field
            code_input = await page.wait_for_selector('input[name="code"], input[autocomplete="one-time-code"]', timeout=10000)
            if code_input:
                otp_needed = True
        except Exception:
            pass

        if otp_needed and not auth_code:
            print("      Verification code requested by Mozilla.")
            if mb:
                print("      Polling Tempik inbox for 6-digit code...")
                otp = mb.code(vendor="firefox", timeout=90.0, interval=3.0)
                if not otp:
                    # Generic 6-digit regex fallback
                    otp = mb.code(pattern=r"\b(\d{6})\b", timeout=60.0)
                if otp:
                    print(f"[+] Found verification code: {otp}")
                    code_input = await page.wait_for_selector('input[name="code"], input[autocomplete="one-time-code"]', timeout=5000)
                    await code_input.fill(otp)
                    await page.keyboard.press("Enter")
                else:
                    print("[-] Failed to retrieve OTP email from Tempik within timeout.")
            else:
                print("[-] Custom email used without Tempik hook. Enter OTP manually if prompted.")

        # Wait up to 15s for redirect if not already captured
        for _ in range(15):
            if auth_code:
                break
            await asyncio.sleep(1)

    if not auth_code:
        sys.exit("[-] Automation finished but no OAuth authorization code was captured.")

    # 5. Exchange code for FxA token & Guardian ProxyPass
    print("[4/5] Exchanging OAuth code for FxA access token...")
    token_resp = fxa_login_cli.exchange_code(auth_code, verifier)
    fxa_token = token_resp.get("access_token")
    if not fxa_token:
        sys.exit(f"[-] Token exchange failed: {token_resp}")

    fxa_login_cli.FXA_TOKEN_FILE.write_text(fxa_token.strip())
    print(f"[+] FxA Access Token acquired successfully!")

    print("[5/5] Activating Mozilla Guardian & Minting Fastly 50 GB ProxyPass...")
    act_res = mozilla_guardian_client.activate_user(fxa_token)
    print(f"      Activation: {act_res}")

    pass_res = mozilla_guardian_client.fetch_proxy_token(fxa_token)
    if "error" in pass_res:
        sys.exit(f"[-] Guardian proxy token error: {pass_res['error']}")

    token = pass_res.get("token")
    if token:
        mozilla_guardian_client.save_token(token)
        acc_log = SCRIPTS_DIR.parent / ".config" / "auto-freecf" / "fxa_accounts.jsonl"
        import json
        acc_log.parent.mkdir(parents=True, exist_ok=True)
        with open(acc_log, "a") as f:
            f.write(json.dumps({
                "ts": int(time.time()),
                "email": email,
                "password": password,
                "fxa_token": fxa_token,
                "guardian_activation": act_res,
            }) + "\n")
        print(f" - Saved Account: {acc_log}")
        claims = mozilla_guardian_client.decode_jwt_claims(token)
        exp = claims.get("exp", 0)
        exp_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(exp)) if exp else "Unknown"
        print(f"\n========================================================")
        print(f" [SUCCESS] Fastly 50 GB VPN Egress Ready!")
        print(f" - Expires   : {exp_str}")
        print(f" - Usage Info: {pass_res.get('usage')}")
        print(f" - Token File: {mozilla_guardian_client.TOKEN_FILE}")
        print(f"========================================================")
        print("Run local proxy now:")
        print("  python3 scripts/fastly_vpn_proxy.py --port 25347")
    else:
        print("[-] Failed retrieving Fastly ProxyPass token:", pass_res)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Automated FxA + Tempik Fastly 50GB Egress")
    parser.add_argument("--email", type=str, default="", help="Existing domain email (optional)")
    parser.add_argument("--password", type=str, default="", help="Account password (optional)")
    parser.add_argument("--headed", action="store_true", help="Run browser in visible mode")
    args = parser.parse_args()

    asyncio.run(
        run_fxa_automation(
            email=args.email or None,
            password=args.password or None,
            headless=not args.headed,
        )
    )
