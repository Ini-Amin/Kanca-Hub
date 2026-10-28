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


def _fetch_persona_avatar(gender: str = "male", out_path: str = "/tmp/persona_avatar.jpg") -> str | None:
    """Download a high quality casual portrait matching the persona identity."""
    import urllib.request, json
    try:
        url = f"https://randomuser.me/api/?nat=us&gender={gender}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
            pic_url = data["results"][0]["picture"]["large"]
        req_pic = urllib.request.Request(pic_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req_pic, timeout=10) as r_pic:
            Path(out_path).write_bytes(r_pic.read())
            print(f"      [✓] Generated matching natural avatar: {pic_url}")
            return out_path
    except Exception as e:
        print(f"      [!] Avatar fetch skipped: {e}")
        return None


def _pick_gender(page):
    try:
        sel = page.locator("select").first
        for opt in ("Female", "Male"):
            try:
                sel.select_option(label=opt)
                return opt
            except Exception:
                continue
    except Exception:
        pass
    return None

def run_facebook_signup(headless: bool = True, country: str = "") -> dict:
    name = persona.full_name(number=False, region="US")
    parts = name.split()
    first_name = re.sub(r'[^a-zA-Z]', '', parts[0])
    last_name = re.sub(r'[^a-zA-Z]', '', parts[-1]) if len(parts) > 1 else "Miller"
    print("\n[1/7] Allocating Tempik domain mailbox on kancalabs.my.id...")
    box = M.open_mailbox("tempik", domain="kancalabs.my.id")
    email = box.address
    print(f"      [✓] Mailbox: {email} (User: {first_name} {last_name})")

    password = persona.password() if hasattr(persona, "password") else "FbTmp!2026xQ"

    proxy_cfg = None
    if country:
        print(f"[0/7] Fetching {country} residential proxy...")
        try:
            import scripts.urban_vpn as uv
            p = uv.get_proxy(country=country)
            proxy_cfg = {
                "server": f"http://{p['host']}:{p['port']}",
                "username": p["username"],
                "password": p["password"],
            }
            print(f"      [✓] UrbanVPN {country}: {p['host']}:{p['port']}")
        except Exception as e:
            print(f"      [!] UrbanVPN {country} failed: {e}")
    if not os.environ.get("XAUTHORITY"):
        xauth = list(Path("/run/user/1000").glob("xauth_*"))
        if xauth:
            os.environ["XAUTHORITY"] = str(xauth[0])
    if not os.environ.get("DISPLAY"):
        os.environ["DISPLAY"] = ":0"

    camoufox_kwargs = {"headless": headless, "humanize": True, "locale": "en-US", "geoip": False}
    if proxy_cfg:
        camoufox_kwargs["proxy"] = proxy_cfg

    print("[2/7] Launching Camoufox anti-detect browser...")
    with Camoufox(**camoufox_kwargs) as browser:
        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"}
        )
        page = context.new_page()

        print("[3/7] Navigating to https://www.facebook.com/reg/?entry_point=login ...")
        page.goto("https://www.facebook.com/reg/?entry_point=login", timeout=40000)
        time.sleep(3)

        inps = page.locator('input')
        inps.nth(0).fill(first_name)
        time.sleep(0.3)
        inps.nth(1).fill(last_name)
        time.sleep(0.3)

        # DOB custom dropdowns
        combos = page.locator('[role="combobox"]')
        try:
            combos.nth(1).click()
            time.sleep(0.5)
            page.evaluate("() => { Array.from(document.querySelectorAll('[role=\"option\"]')).find(el => el.innerText.trim() === 'June').click(); }")
            time.sleep(0.5)
        except Exception as e:
            print("month select err:", e)

        try:
            combos.nth(0).click()
            time.sleep(0.5)
            page.evaluate("() => { Array.from(document.querySelectorAll('[role=\"option\"]')).find(el => el.innerText.trim() === '15').click(); }")
            time.sleep(0.5)
        except Exception as e:
            print("day select err:", e)

        try:
            combos.nth(2).click()
            time.sleep(0.5)
            page.evaluate("() => { Array.from(document.querySelectorAll('[role=\"option\"]')).find(el => el.innerText.trim() === '1998').click(); }")
            time.sleep(0.5)
        except Exception as e:
            print("year select err:", e)

        # Gender custom dropdown
        try:
            combos.nth(3).click()
            time.sleep(0.5)
            page.evaluate("() => { (Array.from(document.querySelectorAll('[role=\"option\"]')).find(el => el.innerText.trim() === 'Female' || el.innerText.trim() === 'Male') || {click:()=>{}}).click(); }")
            time.sleep(0.5)
        except Exception as e:
            print("gender select err:", e)

        # Contact (email) & Password
        inps.nth(2).fill(email)
        time.sleep(0.3)
        inps.nth(3).fill(password)
        time.sleep(0.5)

        print("[4/7] Submitting registration form...")
        submit = page.locator('button:has-text("Submit"), [role="button"]:has-text("Submit"), button[name="websubmit"]').first
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
            if "confirmemail" not in page.url:
                page.goto("https://www.facebook.com/confirmemail.php", timeout=40000)
                time.sleep(4)
            code_input = page.locator('input[type="text"], input').first
            code_input.fill(code)
            time.sleep(1)
            continue_btn = page.locator('[role="button"]:has-text("Continue"), button:has-text("Continue"), button:has-text("Submit")').first
            if continue_btn.is_visible():
                continue_btn.click()
            time.sleep(8)
            print(f"      URL after confirm: {page.url}")

        # Check for checkpoint / upload selfie challenge
        if "checkpoint" in page.url or page.locator('text="Upload a verification selfie", text="Confirm you\'re human"').count() > 0:
            print("      [!] Checkpoint detected on Facebook! Handling verification...")
            # Check if file upload input exists
            file_input = page.locator('input[type="file"]')
            if file_input.count() > 0:
                avatar_path = _fetch_persona_avatar(gender="male")
                if avatar_path and Path(avatar_path).exists():
                    print(f"      [✓] Uploading persona selfie: {avatar_path}")
                    file_input.first.set_input_files(avatar_path)
                    time.sleep(3)
                    submit_btn = page.locator('[role="button"]:has-text("Continue"), button:has-text("Continue")').first
                    if submit_btn.is_visible():
                        submit_btn.click()
                        time.sleep(10)
                        print("      [✓] Checkpoint photo submitted, new URL:", page.url)

        print("[6/7] Simulating light natural activity (warm-up)...")
        try:
            page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=30000)
            time.sleep(3)
            page.mouse.wheel(0, 400)
            time.sleep(2)
            page.mouse.wheel(0, 300)
            time.sleep(2)
        except Exception:
            pass
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
    parser.add_argument("--country", default="", help="US residential proxy country (default: direct)")
    args = parser.parse_args()

    for i in range(args.count):
        print(f"\n=== Creating Facebook Account {i+1} of {args.count} ===")
        try:
            run_facebook_signup(headless=not args.headed, country=args.country)
            if i + 1 < args.count:
                time.sleep(3)
        except Exception as e:
            print(f"[!] Error creating account {i+1}: {e}")


if __name__ == "__main__":
    main()
