#!/usr/bin/env python3
"""scripts/muse_signup_e2e.py — Headless Registration for Muse.ai (Meta Personal AI Agent).

Automates full sign-up using disposable domain email (@kancalabs.my.id via Tempik)
and Camoufox anti-detect browser:
  1. Submits domain email on https://muse.ai/
  2. Extracts 6-digit OTP from Tempik inbox
  3. Fills OTP pin digits into Muse React form
  4. Completes profile (First Name, Last Name, Birthday adult year)
  5. Captures 'hatch_sess' and session cookies for 9Router 'muse-web' node
  6. Saves credentials to ~/.config/auto-freecf/muse_accounts.json
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

# Repo path setup
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.mailboxes as M
import scripts.persona as persona
import scripts.urban_vpn as uv
from camoufox.sync_api import Camoufox

LEDGER_PATH = Path.home() / ".config/auto-freecf/muse_accounts.json"


def run_muse_signup(headless: bool = True, password: str = "", local_part: str = "", country: str = "") -> dict:
    name = persona.full_name()
    parts = name.split()
    first_name = parts[0]
    last_name = parts[-1] if len(parts) > 1 else "Kanca"

    password = password or os.environ.get("MUSE_PASSWORD") or (persona.password() if hasattr(persona, "password") else "MuseTmp!2026xQ")

    print(f"\n[1/6] Allocating Tempik domain mailbox on kancalabs.my.id...")
    box = M.open_mailbox("tempik", domain="kancalabs.my.id", local_part=local_part or os.environ.get("MUSE_LOCAL_PART", ""))
    email = box.address
    print(f"      [✓] Mailbox: {email} (User: {first_name} {last_name})")

    print("[2/6] Launching Camoufox anti-detect browser...")
    proxy_cfg = None
    if country:
        print(f"[0/6] Fetching {country} residential proxy...")
        # Try UrbanVPN first
        try:
            p = uv.get_proxy(country=country)
            # Quick connectivity test
            import subprocess
            test = subprocess.run(
                ["curl", "-s", "-m", "8", "-x", f"http://{p['username']}:{p['password']}@{p['host']}:{p['port']}",
                 "https://api.ipify.org?format=json"],
                capture_output=True, text=True, timeout=12)
            if test.returncode == 0 and test.stdout.strip():
                proxy_cfg = {
                    "server": f"http://{p['host']}:{p['port']}",
                    "username": p["username"],
                    "password": p["password"],
                }
                print(f"      [✓] UrbanVPN {country}: {p['host']}:{p['port']} (exit: {test.stdout.strip()})")
            else:
                raise RuntimeError("UrbanVPN proxy not responding")
        except Exception as e:
            print(f"      [!] UrbanVPN {country} failed: {e}")
            # Fallback to Windscribe for US
            if country.upper() == "US":
                print("      [+] Falling back to Windscribe US...")
                try:
                    import scripts.windscribe_free_proxy as ws
                    creds = ws.get_credentials()
                    hosts = ws.serverlist()
                    us_hosts = [h for k, v in hosts.items() if "us" in k.lower() or "united states" in k.lower() for h in v[:1]]
                    if not us_hosts:
                        us_hosts = [list(hosts.values())[0][0]]
                    host = us_hosts[0]
                    proxy_cfg = {
                        "server": f"https://{host}:443",
                        "username": creds["username"],
                        "password": creds["password"],
                    }
                    print(f"      [✓] Windscribe US: {host}")
                except Exception as e2:
                    print(f"      [!] Windscribe fallback also failed: {e2}")
            else:
                print(f"      [!] No fallback for {country}")

    camoufox_kwargs = {"headless": headless, "humanize": True}
    if proxy_cfg:
        camoufox_kwargs["proxy"] = proxy_cfg  # type: ignore[assignment]
        camoufox_kwargs["geoip"] = True

    with Camoufox(**camoufox_kwargs) as browser:
        page = browser.new_page()

        if proxy_cfg:
            try:
                page.goto("https://api.ipify.org?format=json", timeout=20000)
                time.sleep(2)
                ip_text = page.locator("body").inner_text()
                print(f"      [IP check] Exit IP: {ip_text}")
                page.goto("https://muse.ai/?aymh_complete=1", timeout=40000)
            except Exception as e:
                print(f"      [IP check failed: {e}]")
                page.goto("https://muse.ai/?aymh_complete=1", timeout=40000)
        else:
            print("[3/6] Navigating to https://muse.ai/?aymh_complete=1...")
            page.goto("https://muse.ai/?aymh_complete=1", timeout=40000)

        # Enter email
        inp = page.wait_for_selector('input[placeholder*="Mobile number or email"]', timeout=60000, state="attached")
        inp.fill(email)
        time.sleep(0.5)

        btn = page.wait_for_selector('button:has-text("Continue")', timeout=5000)
        btn.click()

        page.wait_for_selector("text=Enter your code to sign up", timeout=20000)
        print("      [✓] Reached OTP verification screen!")

        print("[4/6] Polling Tempik for Meta 6-digit verification code...")
        otp = None
        end_time = time.time() + 65.0
        while time.time() < end_time and not otp:
            for m in box.read():
                plain = re.sub(r"<[^>]+>", " ", m.get("body") or m.get("text") or "")
                # Prefer a 6-digit code near a keyword; fall back to any 6-digit token
                match = re.search(r"(?:code|verify|verification|otp)[^\d]{0,30}(\d{6})\b", plain, re.IGNORECASE)
                if not match:
                    match = re.search(r"\b(\d{6})\b", plain)
                if match:
                    otp = match.group(1)
                    break
            if not otp:
                time.sleep(2.0)

        if not otp:
            raise RuntimeError("Timed out waiting for Meta OTP from Tempik inbox")

        print(f"      [✓] Received Meta OTP: {otp}")

        print("[5/6] Submitting OTP pin digits...")
        time.sleep(1.0)
        pins = page.locator('[data-slot="pin-digit"]').all()
        for i, d in enumerate(otp):
            pins[i].click()
            time.sleep(0.2)
            page.keyboard.press(d)
            time.sleep(0.1)

        # Wait for "Finish creating your account" profile screen
        page.wait_for_selector("text=Finish creating your account", timeout=20000)
        print("      [✓] Reached Profile Details screen!")

        print(f"[6/6] Completing profile details ({first_name} {last_name}, Year 1998)...")
        page.evaluate(
            """(data) => {
            const fn = document.querySelector('input[placeholder="First name"]');
            const ln = document.querySelector('input[placeholder="Last name"]');
            const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
            setter.call(fn, data.first);
            fn.dispatchEvent(new Event('input', { bubbles: true }));
            setter.call(ln, data.last);
            ln.dispatchEvent(new Event('input', { bubbles: true }));

            const yearSelect = Array.from(document.querySelectorAll('select')).find(s => {
                const hint = (s.name || s.id || s.getAttribute('aria-label') || s.getAttribute('placeholder') || '').toLowerCase();
                return hint.includes('year') || Array.from(s.options).some(o => o.value === '1998');
            });
            if (yearSelect) {
                yearSelect.value = '1998';
                yearSelect.dispatchEvent(new Event('change', { bubbles: true }));
            }
        }""",
            {"first": first_name, "last": last_name},
        )
        time.sleep(1.0)

        confirm_btn = page.wait_for_selector('button[data-slot="button"]:has-text("Confirm")', timeout=5000)
        confirm_btn.click()
        time.sleep(5)
        print("      Waiting for session cookies from Meta / Muse...")
        end_wait = time.time() + 120.0
        last_print = 0
        while time.time() < end_wait:
            cookies_now = page.context.cookies()
            if any(c["name"] == "hatch_sess" for c in cookies_now):
                break
            if "dashboard" in page.url or "home" in page.url:
                break
            now = time.time()
            if now - last_print >= 10:
                print(f"      ...waiting ({int(now - (end_wait - 120))}s) url={page.url} cookies={[c['name'] for c in cookies_now]}")
                last_print = now
            time.sleep(2.0)

        # Extract cookies
        all_cookies = page.context.cookies()
        hatch_cookies = {}
        for c in all_cookies:
            hatch_cookies[c["name"]] = c["value"]

        hatch_sess = hatch_cookies.get("hatch_sess")
        if not hatch_sess:
            raise RuntimeError(f"Registration finished but hatch_sess cookie was not captured: {list(hatch_cookies.keys())}")

        cookie_str = "; ".join([f"{k}={v}" for k, v in hatch_cookies.items()])
        account_entry = {
            "email": email,
            "name": f"{first_name} {last_name}",
            "hatch_sess": hatch_sess,
            "cookie_header": cookie_str,
            "cookies": hatch_cookies,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # Save to ledger
        import fcntl
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        lock_path = LEDGER_PATH.with_suffix(".lock")
        with open(lock_path, "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            ledger = []
            if LEDGER_PATH.exists():
                try:
                    ledger = json.loads(LEDGER_PATH.read_text())
                except Exception:
                    ledger = []
            ledger.append(account_entry)
            tmp = LEDGER_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(ledger, indent=2))
            tmp.replace(LEDGER_PATH)
            fcntl.flock(lf, fcntl.LOCK_UN)

        print(f"\n[✓] MUSE.AI ACCOUNT SUCCESSFULLY CREATED & LOGGED IN!")
        print(f" - Email       : {email}")
        print(f" - User Name   : {first_name} {last_name}")
        print(f" - hatch_sess  : {hatch_sess}")
        print(f" - Saved Ledger: {LEDGER_PATH}")
        return account_entry


def main():
    parser = argparse.ArgumentParser(description="Muse.ai Automated Signup with Domain Email")
    parser.add_argument("--count", "-n", type=int, default=1, help="Number of accounts to create (default: 1)")
    parser.add_argument("--headed", action="store_true", help="Run with visible browser window")
    parser.add_argument("--password", default="", help="Override password (default: persona/env MUSE_PASSWORD)")
    parser.add_argument("--local-part", default="", help="Mailbox local part (default: random/env MUSE_LOCAL_PART)")
    parser.add_argument("--country", default="", help="US/CA residential proxy country (default: direct)")
    args = parser.parse_args()

    for i in range(args.count):
        print(f"\n=== Creating Muse.ai Account {i+1} of {args.count} ===")
        try:
            run_muse_signup(headless=not args.headed, password=args.password, local_part=args.local_part, country=args.country)
            if i + 1 < args.count:
                time.sleep(3)
        except Exception as e:
            print(f"[!] Error creating account {i+1}: {e}")


if __name__ == "__main__":
    main()
