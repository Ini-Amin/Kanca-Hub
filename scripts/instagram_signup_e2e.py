#!/usr/bin/env python3
"""scripts/instagram_signup_e2e.py — Clean Automated Instagram Sign-Up & Age Verification.

Pipeline:
  1. Allocates disposable domain mailbox (@kancalabs.my.id via Tempik).
  2. Generates realistic identity and persona with adult birthdate (>18 years).
  3. Launches Camoufox anti-detect browser via residential proxy.
  4. Fills registration form on https://www.instagram.com/accounts/emailsignup/.
  5. Solves Google reCAPTCHA Enterprise via free audio challenge + speech recognition.
  6. Polls Tempik inbox for Instagram 6-digit confirmation code and submits it.
  7. Captures session cookies (sessionid, csrftoken, ds_user_id).
  8. Saves credentials to ~/.config/auto-freecf/instagram_accounts.json.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Speech recognition for reCAPTCHA audio challenge
import speech_recognition as sr
from pydub import AudioSegment

# Repo imports
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.mailboxes as M
import scripts.persona as persona
import scripts.urban_vpn as uv
from camoufox.sync_api import Camoufox

LEDGER_PATH = Path.home() / ".config/auto-freecf/instagram_accounts.json"


def solve_recaptcha_audio(page) -> bool:
    """Solve reCAPTCHA Enterprise modal inside Instagram referer frame."""
    outer_frame = page.frame_locator("#captcha-recaptcha")
    anchor_frame = outer_frame.frame_locator('iframe[title="reCAPTCHA"]')

    print("      [Captcha] Clicking anchor checkbox...")
    anchor_frame.locator("#recaptcha-anchor").click(timeout=15000)
    time.sleep(3)

    # Check if checkbox solved without challenge
    if anchor_frame.locator("#recaptcha-anchor[aria-checked='true']").count() > 0:
        print("      [✓] reCAPTCHA passed immediately without challenge!")
        next_btn = page.locator('div[role="dialog"] div[role="button"]:has-text("Next"), div[role="button"]:has-text("Next")').first
        if next_btn.count() > 0:
            next_btn.click()
        return True

    bframe = outer_frame.frame_locator('iframe[title*="challenge" i], iframe[src*="bframe" i]')
    solved = False
    for attempt in range(3):
        print(f"      [Captcha] Attempt {attempt+1}: requesting audio challenge...")
        audio_btn = bframe.locator("#recaptcha-audio-button")
        if audio_btn.count() > 0:
            audio_btn.click()
            time.sleep(3)

        if bframe.locator(".rc-doscaptcha-body").count() > 0:
            print("      [!] Google blocked audio queries on this IP (doscaptcha). Needs fresh proxy.")
            return False

        dl = bframe.locator("a.rc-audiochallenge-tdownload-link")
        if dl.count() == 0:
            reload_btn = bframe.locator(".rc-audiochallenge-reload-button")
            if reload_btn.count() > 0:
                reload_btn.click()
                time.sleep(2)
            continue

        audio_url = dl.get_attribute("href")
        print(f"      [Captcha] Downloading audio challenge...")

        with tempfile.TemporaryDirectory() as d:
            mp3 = os.path.join(d, "challenge.mp3")
            wav = os.path.join(d, "challenge.wav")
            urllib.request.urlretrieve(audio_url, mp3)
            AudioSegment.from_file(mp3).export(wav, format="wav")

            recognizer = sr.Recognizer()
            with sr.AudioFile(wav) as source:
                audio_data = recognizer.record(source)
                try:
                    text = recognizer.recognize_google(audio_data)
                except Exception as ex:
                    print(f"      [!] Audio transcription error: {ex}")
                    bframe.locator(".rc-audiochallenge-reload-button").click()
                    time.sleep(2)
                    continue

            print(f"      [✓] Transcribed audio text: '{text}'")
            bframe.locator("#audio-response").fill(text)
            time.sleep(0.5)
            bframe.locator("#recaptcha-verify-button").click()
            time.sleep(3.5)

        if anchor_frame.locator("#recaptcha-anchor[aria-checked='true']").count() > 0:
            print("      [✓] reCAPTCHA checkbox verified checked!")
            solved = True
            break
        else:
            print("      [~] Captcha needs another round, retrying...")
            time.sleep(1.5)

    if solved:
        time.sleep(1)
        next_btn = page.locator('div[role="dialog"] div[role="button"]:has-text("Next"), div[role="button"]:has-text("Next")').first
        if next_btn.count() > 0:
            print("      [Captcha] Clicking Next on confirmation modal...")
            next_btn.click()
            time.sleep(5)
            return True
    return False


def run_instagram_signup(country: str = "SG", headless: bool = True) -> dict:
    print(f"\n[1/6] Allocating Tempik domain mailbox on kancalabs.my.id...")
    box = M.open_mailbox("tempik", domain="kancalabs.my.id")
    email = box.address

    full_name = persona.full_name()
    clean_user = re.sub(r"[^a-zA-Z0-9_]", "_", persona.username())[:12]
    user_name = f"kanca_{clean_user}{random.randint(10, 99)}"
    password = f"{persona.password()}A1!"

    print(f"      [✓] Mailbox  : {email}")
    print(f"      [✓] Profile  : {full_name} (@{user_name})")

    # Get residential proxy
    print(f"[2/6] Acquiring residential proxy ({country})...")
    p = uv.get_proxy(country=country)
    proxy_cfg = {
        "server": f"http://{p['host']}:{p['port']}",
        "username": p["username"],
        "password": p["password"],
    }
    print(f"      [✓] Proxy exit: {p['host']}:{p['port']} ({p['name']})")

    with Camoufox(headless=headless, humanize=True, proxy=proxy_cfg) as browser:
        page = browser.new_page()

        print("[3/6] Navigating to Instagram signup...")
        page.goto("https://www.instagram.com/accounts/emailsignup/", timeout=40000)
        time.sleep(3)

        print("      Filling registration fields...")
        page.locator('label:has-text("Mobile number or email") input').first.fill(email)
        time.sleep(0.2)
        page.locator('label:has-text("Password") input').first.fill(password)
        time.sleep(0.2)
        page.locator('label:has-text("Full name") input').first.fill(full_name)
        time.sleep(0.2)
        page.locator('label:has-text("Username") input').first.fill(user_name)
        time.sleep(0.4)

        print("      Setting birthdate (May 15, 1998 — Adult >18)...")
        page.locator('[role="combobox"][aria-label="Select Month"]').click()
        time.sleep(0.3)
        page.get_by_role("option", name="May", exact=True).click()
        time.sleep(0.3)

        page.locator('[role="combobox"][aria-label="Select Day"]').click()
        time.sleep(0.3)
        page.get_by_role("option", name="15", exact=True).click()
        time.sleep(0.3)

        page.locator('[role="combobox"][aria-label="Select Year"]').click()
        time.sleep(0.3)
        page.get_by_role("option", name="1998", exact=True).click()
        time.sleep(0.5)

        print("      Submitting form...")
        page.locator('[role="button"]:has-text("Submit")').first.click()
        time.sleep(6)

        # Check for reCAPTCHA challenge
        if page.locator("#captcha-recaptcha").count() > 0:
            print("[4/6] reCAPTCHA challenge detected! Solving via audio...")
            solved = solve_recaptcha_audio(page)
            if not solved:
                page.screenshot(path="/tmp/ig_captcha_failed.png")
                raise RuntimeError("Failed to solve reCAPTCHA challenge")
        else:
            print("[4/6] No captcha challenged on submission.")

        # Wait for "Enter the confirmation code" screen
        print("[5/6] Waiting for confirmation code screen...")
        page.wait_for_selector("text=Enter the confirmation code", timeout=35000)
        print("      [✓] Reached OTP confirmation screen!")

        print(f"      Polling Tempik for Instagram 6-digit confirmation code...")
        otp = None
        end_time = time.time() + 75.0
        while time.time() < end_time and not otp:
            for m in box.read():
                subj = m.get("subject") or ""
                body = m.get("body") or m.get("text") or ""
                match = re.search(r"\b(\d{6})\b", f"{subj} {body}")
                if match:
                    otp = match.group(1)
                    break
            if not otp:
                time.sleep(2.5)

        if not otp:
            page.screenshot(path="/tmp/ig_otp_timeout.png")
            raise RuntimeError(f"Timed out waiting for Instagram OTP in inbox {email}")

        print(f"      [✓] Received Instagram Code: {otp}")

        print("[6/6] Submitting confirmation code...")
        code_input = page.locator('label:has-text("Confirmation code") input, input:visible').first
        code_input.click()
        time.sleep(0.3)
        code_input.press_sequentially(otp, delay=120)
        time.sleep(1.5)

        for attempt in range(2):
            continue_btn = page.locator('[role="button"]:has-text("Continue"), button:has-text("Continue")').first
            continue_btn.click()
            time.sleep(6)
            page.screenshot(path=f"/tmp/ig_code_attempt_{int(time.time())}.png")
            body_now = page.inner_text("body")
            if "invalid or has expired" in body_now.lower():
                print(f"      [!] code rejected (attempt {attempt+1}) — requesting a fresh one…")
                resend = page.locator('div[role="button"]:has-text("I didn\'t get the code"), '
                                      'button:has-text("I didn\'t get the code")').first
                if resend.count() > 0:
                    resend.click()
                    time.sleep(4)
                # poll for a NEWER mail: track which codes we already tried
                seen_codes = {otp}
                fresh = None
                end_time = time.time() + 75.0
                while time.time() < end_time and not fresh:
                    for m in box.read():
                        match = re.search(r"\b(\d{6})\b",
                                          f"{m.get('subject','')} {m.get('body','') or ''}")
                        if match and match.group(1) not in seen_codes:
                            fresh = match.group(1)
                            break
                    if not fresh:
                        time.sleep(2.5)
                if not fresh:
                    page.screenshot(path=f"/tmp/ig_fresh_otp_timeout_{int(time.time())}.png")
                    raise RuntimeError("resend gave no NEW code within 75 s")
                otp = fresh
                print(f"      [✓] new code: {otp}")
                code_input.click()
                time.sleep(0.2)
                code_input.press_sequentially(otp, delay=120)
                time.sleep(1.0)
                continue
            break

        # END-STATE CHECK: success is only real if we left the signup flow with a session
        page.screenshot(path=f"/tmp/ig_post_confirm_{int(time.time())}.png")
        cookies = {c["name"]: c["value"] for c in page.context.cookies() if "instagram.com" in c["domain"]}
        final_url = page.url
        left_signup = "emailsignup" not in final_url and "code" not in final_url.lower()
        if not (left_signup and "sessionid" in cookies):
            print(f"      [!] end-state FAILED: url={final_url}  sessionid={'yes' if 'sessionid' in cookies else 'no'}")
            print("      body tail:", re.sub(r"\s+", " ", body_now)[:300])
            raise RuntimeError("code submitted but account not confirmed — end-state check failed")
        print("      [✓] end-state verified: confirmed + session cookies present")
        ig_cookies = cookies
        session_id = ig_cookies.get("sessionid")

        print(f"\n[✓] INSTAGRAM ACCOUNT SUCCESSFULLY CREATED!")
        print(f" - Email       : {email}")
        print(f" - Username    : {user_name}")
        print(f" - Password    : {password}")
        print(f" - sessionid   : {session_id[:25] if session_id else 'pending_first_login'}...")
        print(f" - URL         : {page.url}")

        acc_record = {
            "email": email,
            "username": user_name,
            "password": password,
            "full_name": full_name,
            "birth_year": 1998,
            "sessionid": session_id,
            "cookies": ig_cookies,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        ledger = []
        if LEDGER_PATH.exists():
            try:
                ledger = json.loads(LEDGER_PATH.read_text())
            except Exception:
                ledger = []
        ledger.append(acc_record)
        LEDGER_PATH.write_text(json.dumps(ledger, indent=2))
        print(f" - Saved to    : {LEDGER_PATH}")

        page.screenshot(path="/tmp/ig_registered_success.png")
        return acc_record


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--recover":
        # usage: instagram_signup_e2e.py --recover EMAIL [NEW_PASSWORD]
        email = sys.argv[2]
        newpw = sys.argv[3] if len(sys.argv) > 3 else f"{persona.password()}A1!"
        ok = run_recover(email, newpw, headless=True, country="JP")
        sys.exit(0 if ok else 1)
    parser = argparse.ArgumentParser(description="Instagram Automated Sign-Up")
    parser.add_argument("-n", "--count", type=int, default=1, help="Number of accounts to create (default: 1)")
    parser.add_argument("--country", default="SG", help="Residential proxy country code (default: SG)")
    parser.add_argument("--headed", action="store_true", help="Run with visible browser window")
    args = parser.parse_args()

    for i in range(args.count):
        print(f"\n==========================================")
        print(f" Creating Instagram Account {i+1} of {args.count}")
        print(f"==========================================")
        try:
            run_instagram_signup(country=args.country, headless=not args.headed)
            if i + 1 < args.count:
                time.sleep(5)
        except Exception as e:
            print(f"[!] Registration failed: {e}")


def run_recover(email: str, new_password: str, headless: bool = True,
                country: str = "JP") -> bool:
    """Password-reset resume: finalize a half-created / unreachable account.

    Reset mail arriving  ⇒ the account EXISTS in Meta's system and we regain it.
    "no account" answer  ⇒ signup never truly registered it.
    """
    from scripts.mailboxes import open_mailbox  # local import keeps script importable
    box = open_mailbox("tempik", domain="kancalabs.my.id", local_part=email.split("@")[0])

    p = uv.get_proxy(country=country)
    proxy_cfg = {"server": f"http://{p['host']}:{p['port']}",
                 "username": p["username"], "password": p["password"]}
    with Camoufox(headless=headless, humanize=True, proxy=proxy_cfg) as browser:
        page = browser.new_page()
        page.goto("https://www.instagram.com/accounts/login/", timeout=40000)
        time.sleep(4)
        resp_status = page.evaluate(
            """async (email) => {
                const tok = (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || "";
                const r = await fetch('/accounts/password/reset/', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/x-www-form-urlencoded',
                              'X-Requested-With': 'XMLHttpRequest',
                              'X-CSRFToken': tok},
                    body: new URLSearchParams({usemail: email}).toString(),
                    credentials: 'include'});
                return r.status + ' :: ' + (await r.text()).slice(0, 300);
            }""", email)
        print(f"[recover] POST /accounts/password/reset/ → {str(resp_status)[:200]}")
        (Path.home() / ".config/auto-freecf/threads_scratch" / "dom_reset.txt").write_text(
            str(resp_status))

        reset_url = None
        end_time = time.time() + 120.0
        while time.time() < end_time and not reset_url:
            for m in box.read():
                blob = f"{m.get('subject','')} {m.get('body','')} {m.get('html','')}"
                mm = re.search(r'https?://[^\s"\'<>]*(?:password_reset|reset/password)[^\s"\'<>]+',
                               blob, re.I)
                if mm:
                    reset_url = mm.group(0).replace("&amp;", "&")
                    break
            if not reset_url:
                time.sleep(3)

        if not reset_url:
            print("[recover] no reset mail after 120 s — account likely NOT registered.")
            print("          answer page said:")
            ans = (Path.home() / ".config/auto-freecf/threads_scratch" / "dom_reset.txt").read_text()[:300]
            print(ans)
            return False

        print(f"[recover] reset link captured → {reset_url[:80]}…")
        page.goto(reset_url, timeout=40000)
        time.sleep(4)
        pws = page.locator('input[type="password"]')
        n = pws.count()
        for i in range(n):
            pws.nth(i).fill(new_password)
            time.sleep(0.3)
        if n == 0:
            page.screenshot(path="/tmp/ig_recover_no_pwfield.png")
            print("[recover] no password fields on the reset page — inspect /tmp shot")
            return False
        page.locator('button:has-text("Reset password"), div[role="button"]:has-text("Log in"), '
                     'button[type="submit"]').first.click()
        time.sleep(8)
        (Path.home() / ".config/auto-freecf/threads_scratch" / "dom_after_reset.txt").write_text(
            page.inner_text("body")[:2000])
        cookies = {c["name"]: c["value"] for c in page.context.cookies() if "instagram.com" in c["domain"]}
        timed_in = "sessionid" in cookies
        print(f"[recover] final url: {page.url}   sessionid: {timed_in}")
        if timed_in:
            LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
            try:
                ledger = json.loads(LEDGER_PATH.read_text())
            except Exception:
                ledger = []
            for a in ledger:
                if a.get("email") == email:
                    a["password"] = new_password
                    a["sessionid"] = cookies.get("sessionid")
                    a["cookies"] = cookies
            LEDGER_PATH.write_text(json.dumps(ledger, indent=2))
            print(f"[recover] ledger updated for {email}")
        return timed_in


if __name__ == "__main__":
    main()
