#!/usr/bin/env python3
"""scripts/facebook_signup_e2e.py — Headless Registration for Facebook (Meta).

Automates full sign-up using disposable domain email (@kancalabs.my.id via Tempik)
and Camoufox anti-detect browser:
  1. Submits domain email on https://www.facebook.com/reg/
  2. Fills name, DOB, gender, password
  3. Extracts 6-digit OTP from Tempik inbox (if Meta emails a code)
  4. Captures session cookies (c_user, xs) for 9Router 'facebook-web' node
  5. Saves credentials to ~/.config/auto-freecf/facebook_accounts.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.mailboxes as M
import scripts.persona as persona
from camoufox.sync_api import Camoufox

LEDGER_PATH = Path.home() / ".config/auto-freecf/facebook_accounts.json"


def _pick_gender(page):
    try:
        sel = page.locator("select").first
        # Facebook uses a select with Male/Female/Custom
        for opt in ("Female", "Male"):
            try:
                sel.select_option(label=opt)
                return opt
            except Exception:
                continue
    except Exception:
        pass
    return None


def run_facebook_signup(headless: bool = True) -> dict:
    name = persona.full_name()
    parts = name.split()
    first_name = parts[0]
    last_name = parts[-1] if len(parts) > 1 else "Kanca"

    print("\n[1/7] Allocating Tempik domain mailbox on kancalabs.my.id...")
    box = M.open_mailbox("tempik", domain="kancalabs.my.id")
    email = box.address
    print(f"      [✓] Mailbox: {email} (User: {first_name} {last_name})")

    password = persona.password() if hasattr(persona, "password") else "FbTmp!2026xQ"

    print("[2/7] Launching Camoufox anti-detect browser...")
    with Camoufox(headless=headless, humanize=True) as browser:
        page = browser.new_page()

        print("[3/7] Navigating to https://www.facebook.com/reg/?entry_point=login ...")
        page.goto("https://www.facebook.com/reg/?entry_point=login", timeout=40000)
        time.sleep(2)

        # First name / surname
        page.get_by_label("First name").first.click()
        time.sleep(0.3)
        page.get_by_label("First name").first.fill(first_name)
        time.sleep(0.5)
        # Surname: Meta renders two inputs; fill all of them to be safe
        for s in page.get_by_label("Surname").all():
            try:
                s.click()
                time.sleep(0.2)
                s.fill(last_name)
                time.sleep(0.3)
            except Exception as e:
                print("surname fill err", e)

        # DOB custom dropdowns
        for label, value in (("Day", "15"), ("Month", "June"), ("Year", "1998")):
            try:
                page.get_by_label(label).first.click()
                time.sleep(0.8)
                page.get_by_text(value, exact=True).first.click()
                time.sleep(0.5)
            except Exception as e:
                print("dob err", label, e)

        # Gender custom dropdown (4th combobox has no label)
        try:
            page.get_by_role("combobox").nth(3).click()
            time.sleep(0.8)
            page.get_by_text("Female", exact=True).first.click()
            time.sleep(0.5)
        except Exception as e:
            print("gender err", e)
            try:
                page.get_by_role("combobox").nth(3).click()
                time.sleep(0.8)
                page.get_by_text("Male", exact=True).first.click()
                time.sleep(0.5)
            except Exception as e2:
                print("gender fallback err", e2)

        # Contact (email)
        page.get_by_label("Mobile number or email address").first.click()
        time.sleep(0.3)
        page.get_by_label("Mobile number or email address").first.fill(email)
        time.sleep(0.5)

        # Password
        page.get_by_label("Password").first.click()
        time.sleep(0.3)
        page.get_by_label("Password").first.fill(password)
        time.sleep(0.5)

        print("[4/7] Submitting registration form...")
        submit = page.get_by_role("button", name="Submit").first
        submit.click()
        time.sleep(6)

        print("[5/7] Checking for email verification code...")
        # Meta sends a 5-digit confirmation code (not a link)
        code = None
        end_time = time.time() + 180.0
        while time.time() < end_time and not code:
            for m in box.read():
                blob = " ".join(str(m.get(k) or "") for k in ("subject", "text", "body", "html", "source", "message"))
                mcode = re.search(r"\b(\d{5})\b.*confirmation code", blob, re.IGNORECASE)
                if not mcode:
                    mcode = re.search(r"confirmation code[^0-9]*(\d{5})\b", blob, re.IGNORECASE)
                if not mcode:
                    mcode = re.search(r"\b(\d{5})\s+is your", blob, re.IGNORECASE)
                if mcode:
                    code = mcode.group(1)
                    break
            if not code:
                time.sleep(5.0)

        if code:
            print(f"      [✓] Received confirmation code: {code}")
            # Navigate to confirmemail if not already there
            if "confirmemail" not in page.url:
                page.goto("https://www.facebook.com/confirmemail.php", timeout=40000)
                time.sleep(4)
            code_input = page.locator("input").first
            code_input.fill(code)
            time.sleep(1)
            try:
                page.get_by_role("button", name="Continue").first.click()
            except Exception:
                try:
                    page.get_by_role("button", name="Submit").first.click()
                except Exception:
                    pass
            time.sleep(8)
            print(f"      URL after confirm: {page.url}")
        else:
            print("      [i] No verification code received — continuing anyway.")

        print("[6/7] Waiting for session cookies from Meta / Facebook...")
        time.sleep(10)

        all_cookies = page.context.cookies()
        cookie_map = {c["name"]: c["value"] for c in all_cookies}
        c_user = cookie_map.get("c_user")
        xs = cookie_map.get("xs")

        if not c_user or not xs:
            raise RuntimeError(
                f"Registration finished but c_user/xs cookie was not captured: {list(cookie_map.keys())}")

        cookie_str = "; ".join([f"{k}={v}" for k, v in cookie_map.items()])
        account_entry = {
            "email": email,
            "name": f"{first_name} {last_name}",
            "password": password,
            "c_user": c_user,
            "xs": xs,
            "cookie_header": cookie_str,
            "cookies": cookie_map,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        ledger = []
        if LEDGER_PATH.exists():
            try:
                ledger = json.loads(LEDGER_PATH.read_text())
            except Exception:
                ledger = []
        ledger.append(account_entry)
        LEDGER_PATH.write_text(json.dumps(ledger, indent=2))

        print("\n[✓] FACEBOOK ACCOUNT SUCCESSFULLY CREATED & LOGGED IN!")
        print(f" - Email       : {email}")
        print(f" - User Name   : {first_name} {last_name}")
        print(f" - c_user      : {c_user}")
        print(f" - Saved Ledger: {LEDGER_PATH}")
        return account_entry


def main():
    parser = argparse.ArgumentParser(description="Facebook Automated Signup with Domain Email")
    parser.add_argument("--count", "-n", type=int, default=1, help="Number of accounts to create (default: 1)")
    parser.add_argument("--headed", action="store_true", help="Run with visible browser window")
    args = parser.parse_args()

    for i in range(args.count):
        print(f"\n=== Creating Facebook Account {i+1} of {args.count} ===")
        try:
            run_facebook_signup(headless=not args.headed)
            if i + 1 < args.count:
                time.sleep(3)
        except Exception as e:
            print(f"[!] Error creating account {i+1}: {e}")


if __name__ == "__main__":
    main()
