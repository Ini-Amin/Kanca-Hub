#!/usr/bin/env python3
"""scripts/github_signup_interactive.py — Semi-automated GitHub registration helper.

Automates the tedious steps while letting a human solve the Arkose/DataDome challenge:
  1. Generates disposable email on @kancalabs.my.id (Tempik) & realistic persona.
  2. Opens Camoufox on user's desktop display (:0) without UBO adblocker (bypasses DataDome noscript block).
  3. Pre-fills email, password, username, and notification preferences.
  4. Waits for human to complete the visual Arkose captcha on screen.
  5. Automatically catches the 8-digit launch code from Tempik in real-time and types it.
  6. Saves confirmed account to ~/Auto-FreeCF/github_accounts.json.

Usage:
  /home/amen/.local/share/auto-freecf/camoufox-venv/bin/python scripts/github_signup_interactive.py [--proxy PROXY]
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import random
import re
import string
import sys
import time
from pathlib import Path

# Add repo scripts to sys.path
SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

# Ensure XAUTHORITY is set for desktop display
if not os.environ.get("XAUTHORITY"):
    xauths = sorted(glob.glob("/run/user/1000/xauth_*"), key=os.path.getmtime, reverse=True)
    if xauths:
        os.environ["XAUTHORITY"] = xauths[0]

from camoufox.async_api import AsyncCamoufox
from camoufox import DefaultAddons
import camoufox_helpers
import mailboxes as M
import persona

ACCOUNTS_FILE = REPO_ROOT / "github_accounts.json"


def generate_password() -> str:
    alphabet = string.ascii_letters + string.digits
    symbols = "!@#$%^&*"
    chars = [
        random.choice(string.ascii_uppercase),
        random.choice(string.ascii_lowercase),
        random.choice(string.digits),
        random.choice(symbols),
    ]
    chars += [random.choice(alphabet + symbols) for _ in range(12)]
    random.shuffle(chars)
    return "".join(chars)


def generate_username() -> str:
    try:
        u = persona.username(max_len=18)
        u_clean = re.sub(r"[^a-zA-Z0-9-]", "", u).strip("-")
        if u_clean:
            return u_clean + str(random.randint(10, 99))
    except Exception:
        pass
    return "dev" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))


def save_account(email: str, password: str, username: str):
    data = {"accounts": []}
    if ACCOUNTS_FILE.exists():
        try:
            raw = json.loads(ACCOUNTS_FILE.read_text())
            if isinstance(raw, dict) and "accounts" in raw:
                data = raw
            elif isinstance(raw, list):
                data = {"accounts": raw}
        except Exception:
            pass

    data.setdefault("accounts", []).append({
        "username": username,
        "password": password,
        "email": email,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "source": "camoufox-interactive",
    })
    data["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    ACCOUNTS_FILE.write_text(json.dumps(data, indent=2))
    print(f"\n[✓] Account saved to {ACCOUNTS_FILE}")


async def fill_field(page, selector: str, text: str, delay: float = 0.5):
    el = await page.wait_for_selector(selector, state="visible", timeout=15000)
    await el.click()
    await asyncio.sleep(0.3)
    await el.type(text, delay=random.uniform(40, 90))
    await asyncio.sleep(delay)


async def click_continue(page, button_selector: str = "button[data-continue-to], button:has-text('Continue')"):
    try:
        btn = await page.wait_for_selector(button_selector, state="visible", timeout=6000)
        await asyncio.sleep(0.5)
        await btn.click()
        await asyncio.sleep(1.5)
    except Exception:
        pass


async def run_interactive(proxy: str | None = None):
    print("=" * 60)
    print("  GitHub Semi-Automated Registration (Camoufox Interactive)")
    print("=" * 60)

    # 1. Allocate Tempik mailbox
    local_part = "gh" + "".join(random.choices(string.ascii_lowercase + string.digits, k=7))
    print(f"[1/4] Mengalokasikan mailbox Tempik: {local_part}@kancalabs.my.id...")
    box = M.open_mailbox("tempik", domain="kancalabs.my.id", local_part=local_part)
    email = box.address
    password = generate_password()
    username = generate_username()

    print(f"      • Email    : {email}")
    print(f"      • Username : {username}")
    print(f"      • Password : {password}")

    # 2. Launch Camoufox on desktop
    proxy_cfg = camoufox_helpers.to_camoufox_proxy(proxy) if proxy else None
    print(f"[2/4] Membuka browser Camoufox di desktop (Proxy: {proxy or 'Direct'})...")

    kwargs = {
        "headless": False,
        "humanize": True,
        "os": "windows",
        "exclude_addons": [DefaultAddons.UBO],  # bypass DataDome noscript block
    }
    if proxy_cfg:
        kwargs["proxy"] = proxy_cfg
        kwargs["geoip"] = True

    async with AsyncCamoufox(**kwargs) as browser:
        page = await browser.new_page()

        print("[3/4] Membuka https://github.com/signup...")
        await page.goto("https://github.com/signup", timeout=45000, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        # Pre-fill steps
        print("      Mengisi email...")
        try:
            await fill_field(page, "input#email, input[name='email']", email)
            await click_continue(page, "button[data-continue-to='password-container'], button:has-text('Continue')")
        except Exception as e:
            print(f"      [!] Gagal auto-fill email ({e}); silakan ketik manual di browser.")

        print("      Mengisi password...")
        try:
            await fill_field(page, "input#password, input[name='password']", password)
            await click_continue(page, "button[data-continue-to='username-container'], button:has-text('Continue')")
        except Exception as e:
            print(f"      [!] Gagal auto-fill password ({e}); silakan ketik manual.")

        print("      Mengisi username...")
        try:
            await fill_field(page, "input#login, input[name='login']", username)
            await click_continue(page, "button[data-continue-to='opt-in-container'], button:has-text('Continue')")
        except Exception as e:
            print(f"      [!] Gagal auto-fill username ({e}); silakan ketik manual.")

        print("      Mengisi preferensi notifikasi (n)...")
        try:
            await fill_field(page, "input#opt_in, input[name='opt_in']", "n")
            await click_continue(page, "button[data-continue-to='captcha-and-submit-container'], button:has-text('Continue')")
        except Exception:
            pass

        print("\n" + "=" * 60)
        print("  👉 [ACTION REQUIRED / TINDAKAN DIPERLUKAN]")
        print("  Silakan selesaikan puzzle verifikasi Arkose / Captcha di layar.")
        print("  Skrip otomatis memantau kode verifikasi 8-digit dari Tempik...")
        print("=" * 60 + "\n")

        # 3. Poll for GitHub Launch Code
        start_time = time.time()
        launch_code = None
        while time.time() - start_time < 300:
            # Check if URL transitioned to dashboard already
            cur_url = page.url
            if any(k in cur_url for k in ["/dashboard", "/welcome", "/onboarding"]):
                print(f"\n[✓] Berhasil masuk ke dashboard: {cur_url}")
                save_account(email, password, username)
                await asyncio.sleep(5)
                return

            # Check inbox
            code = box.code(vendor="github", timeout=2.0)
            if code and len(code) == 8:
                launch_code = code
                print(f"\n[+] KODE VERIFIKASI DITEMUKAN: {launch_code}")
                break

            await asyncio.sleep(3.0)

        # 4. Fill launch code into the page
        if launch_code:
            print(f"[4/4] Memasukkan kode verifikasi 8-digit ({launch_code}) ke halaman...")
            boxes = await page.query_selector_all("input[data-test-selector='launch-code-box'], input.otp-input")
            if len(boxes) >= 8:
                for idx_box, digit in enumerate(launch_code):
                    await boxes[idx_box].fill(digit)
                    await asyncio.sleep(0.1)
            else:
                input_field = await page.query_selector("input#launch-code, input[name='launch-code'], input[autocomplete='one-time-code']")
                if input_field:
                    await input_field.fill(launch_code)
                    await asyncio.sleep(0.5)

            # Wait for dashboard transition
            print("      Menunggu konfirmasi akun...")
            for _ in range(15):
                await asyncio.sleep(2)
                if any(k in page.url for k in ["/dashboard", "/welcome", "/onboarding", "/settings"]):
                    print(f"\n[✓] Akun berhasil terverifikasi! (URL: {page.url})")
                    save_account(email, password, username)
                    await asyncio.sleep(4)
                    return

        print("\n[ℹ️] Sesi selesai. Jika akun sudah login, kredensial tetap tercatat:")
        print(f"     Email: {email} | User: {username} | Pass: {password}")
        save_account(email, password, username)
        await asyncio.sleep(8)


def main():
    parser = argparse.ArgumentParser(description="GitHub Semi-Automated Registration")
    parser.add_argument("--proxy", default=None, help="Proxy URL (e.g. http://host:port)")
    args = parser.parse_args()

    asyncio.run(run_interactive(proxy=args.proxy))


if __name__ == "__main__":
    main()
