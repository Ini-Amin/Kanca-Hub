#!/usr/bin/env python3
"""scripts/freebuff_login_auto.py — Automated Freebuff magic link sender & session capture.

Flow:
  1. Requests auth_code & loginUrl from freebuff.com/api/auth/cli/code with slot's fingerprint.
  2. Uses Camoufox with the slot's sticky proxy to open the loginUrl, types the Gmail plus-address,
     and clicks Continue to send the magic link.
  3. Polls freebuff.com/api/auth/cli/status while waiting for the user to click the email link.
  4. On approval, saves credentials.json to the slot directory.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import mailboxes
BASE_DIR = Path.home() / ".config" / "auto-freecf" / "freebuff_slots"


def load_slot_data(slot_name: str) -> tuple[Path, str, str]:
    slot_dir = BASE_DIR / slot_name
    if not slot_dir.exists():
        sys.exit(f"Slot directory not found: {slot_dir}")
    anon_file = slot_dir / "analytics-id.json"
    if not anon_file.exists():
        sys.exit(f"analytics-id.json not found in {slot_dir}")
    anon_id = json.loads(anon_file.read_text("utf-8")).get("anonymousId")

    meta_file = slot_dir / "slot_meta.json"
    proxy = ""
    if meta_file.exists():
        try:
            proxy = json.loads(meta_file.read_text("utf-8")).get("proxy", "")
        except Exception:
            pass
    return slot_dir, anon_id, proxy


async def submit_email_form(login_url: str, email: str, proxy: str | None) -> bool:
    from camoufox.async_api import AsyncCamoufox

    proxy_cfg = None
    if proxy and proxy != "(direct)":
        proxy_cfg = {"server": proxy}

    print(f"[*] Opening login page via Camoufox (proxy: {proxy or '(direct)'})...")
    async with AsyncCamoufox(headless=True, proxy=proxy_cfg) as browser:
        page = await browser.new_page()
        try:
            resp_info = None
            async def handle_resp(r):
                nonlocal resp_info
                if "/api/auth/email" in r.url:
                    try:
                        t = await r.text()
                        resp_info = (r.status, t)
                    except Exception:
                        pass
            page.on("response", handle_resp)

            await page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2500)

            print(f"[*] Typing email: {email}...")
            await page.fill('input[type="email"]', email, timeout=8000)
            await page.wait_for_timeout(500)

            btn = page.locator('button[type="submit"]:has-text("Continue")').first
            await btn.click(timeout=8000)
            await page.wait_for_timeout(4000)

            print(f"[*] Submit response: {resp_info}")
            body_txt = await page.evaluate("() => document.body.innerText")
            if "Check your email" in body_txt or (resp_info and resp_info[0] == 200):
                print("[✓] Magic link successfully dispatched by Freebuff!")
                return True
            else:
                print(f"[!] Warning: Email submit did not confirm: {body_txt[:200]}")
                return False
        except Exception as e:
            print(f"[✗] Error submitting email: {e}")
            return False


def request_cli_code(fingerprint_id: str, proxy: str | None) -> dict:
    url = "https://freebuff.com/api/auth/cli/code"
    data = json.dumps({"fingerprintId": fingerprint_id}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener()
    if proxy and proxy != "(direct)":
        opener.add_handler(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    with opener.open(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def poll_status(fingerprint_id: str, fingerprint_hash: str, expires_at: int, proxy: str | None, timeout_s: int = 300) -> dict | None:
    opener = urllib.request.build_opener()
    if proxy and proxy != "(direct)":
        opener.add_handler(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))

    url = (f"https://freebuff.com/api/auth/cli/status?fingerprintId={fingerprint_id}"
           f"&fingerprintHash={fingerprint_hash}&expiresAt={expires_at}")

    t0 = time.time()
    attempt = 0
    while time.time() - t0 < timeout_s:
        attempt += 1
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with opener.open(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                user = data.get("user")
                if user and isinstance(user, dict):
                    return user
        except urllib.error.HTTPError as e:
            if e.code != 401:
                print(f"  [poll] HTTP {e.code}: {e.reason}")
        except Exception as e:
            print(f"  [poll] Network error: {e}")

        time.sleep(4)
        if attempt % 5 == 0:
            remaining = int(timeout_s - (time.time() - t0))
            print(f"  ... still waiting for email link click ({remaining}s remaining)")
    return None


async def main_async(args: argparse.Namespace) -> None:
    slot_name = args.slot
    slot_dir, anon_id, proxy = load_slot_data(slot_name)

    # 1. Obtain dynamic @gmail.com mailbox via emailmux if auto
    box = None
    if not args.email or args.email == "auto":
        print(f"[*] Provisioning real @gmail.com inbox via emailmux for [{slot_name}]...")
        box = mailboxes.open_mailbox("emailmux")
        email = box.address
    else:
        email = args.email

    print(f"\n=== Automated Freebuff Login for [{slot_name}] ===")
    print(f"  Target Email:   {email}")
    print(f"  Fingerprint ID: {anon_id}")
    print(f"  Sticky Proxy:   {proxy or '(direct)'}")
    print("\n[1/3] Requesting CLI login code from Freebuff...")
    code_info = request_cli_code(anon_id, proxy)
    login_url = code_info.get("loginUrl")
    fp_hash = code_info.get("fingerprintHash")
    expires_at = code_info.get("expiresAt")
    print(f"  Login URL: {login_url}")

    # 2. Open login page in Camoufox and submit email
    print("\n[2/3] Automating email submission via browser...")
    ok = await submit_email_form(login_url, email, proxy)
    if not ok:
        print("\n[!] Browser submit was inconclusive. You can open the link above manually if needed.")

    # 3. Auto-consume magic link if using emailmux, else wait for manual click
    if box:
        print(f"\n[3/3] Polling emailmux inbox for Freebuff magic link ({email})...")
        magic_url = box.link(r"https://[^\s\"'>]*freebuff\.com/[^\s\"'>]*", timeout=120)
        if magic_url:
            print(f"[✓] Magic link captured: {magic_url[:65]}...")
            print("[*] Consuming magic link via HTTP GET...")
            req = urllib.request.Request(
                magic_url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            opener = urllib.request.build_opener()
            if proxy and proxy != "(direct)":
                opener.add_handler(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
            try:
                with opener.open(req, timeout=15) as r:
                    print(f"[✓] Magic link consumed (HTTP {r.status})")
            except Exception as e:
                print(f"[!] Link consumption notice: {e}")
        else:
            print("[!] Timeout waiting for magic link in emailmux inbox.")
    else:
        print(f"\n[3/3] WAITING FOR LINK CLICK IN INBOX: {email}")
        print(f"  --> Buka inbox Gmail kamu ({email.split('+')[0]}@gmail.com)")
        print(f"  --> Buka email dari Freebuff dan klik tombol 'Sign in to Freebuff'")

    user = poll_status(anon_id, fp_hash, expires_at, proxy, timeout_s=args.timeout)
    if not user:
        sys.exit("\n[✗] Login polling timed out or failed.")
    creds_file = slot_dir / "credentials.json"
    creds_data = {
        "default": {
            **user,
            "name": user.get("name") or "",
        }
    }
    creds_file.write_text(json.dumps(creds_data, indent=2), "utf-8")
    os.chmod(creds_file, 0o600)

    # Update slot_meta
    meta_file = slot_dir / "slot_meta.json"
    meta = {}
    if meta_file.exists():
        try: meta = json.loads(meta_file.read_text("utf-8"))
        except Exception: pass
    meta["status"] = "active"
    meta["user_email"] = user.get("email", email)
    meta["user_id"] = user.get("id", "")
    meta_file.write_text(json.dumps(meta, indent=2), "utf-8")

    print(f"\n[✓✓✓] SUCCESS! Logged in as: {user.get('name') or user.get('email')}")
    print(f"      Auth token saved to: {creds_file}")
    print(f"      Slot [{slot_name}] is now fully authenticated and ready to use!\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Automate Freebuff login via emailmux @gmail.com")
    ap.add_argument("--slot", default="slot_1", help="Slot to authenticate (default: slot_1)")
    ap.add_argument("--email", default="auto", help="Email to use ('auto' for dynamic emailmux @gmail.com)")
    ap.add_argument("--timeout", type=int, default=300, help="Polling timeout in seconds")
    args = ap.parse_args()
    asyncio.run(main_async(args))

if __name__ == "__main__":
    main()
