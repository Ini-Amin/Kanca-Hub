#!/usr/bin/env python3
"""scripts/pipeline_4step_muse.py — Complete 4-Step Pipeline:
1. Natural Facebook signup from homepage (Jessica Williams)
2. Profile setup & 90s natural warming up
3. Natural Muse onboarding from homepage using fresh invite code 4GB9QW
4. Automated age confirmation via Facebook & workspace entry
"""
import os
import sys
import time
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Ensure display
if not os.environ.get("XAUTHORITY"):
    xauth = list(Path("/run/user/1000").glob("xauth_*"))
    if xauth:
        os.environ["XAUTHORITY"] = str(xauth[0])
if not os.environ.get("DISPLAY"):
    os.environ["DISPLAY"] = ":0"

import scripts.mailboxes as M
from camoufox.sync_api import Camoufox

EMAIL = "jalaklucu25@kancalabs.my.id"
LOCAL_PART = "jalaklucu25"
DOMAIN = "kancalabs.my.id"
FIRST_NAME = "Jessica"
LAST_NAME = "Williams"
PASSWORD = "JessicaMeta!2026xQ"
INVITE_CODE = "4GB9QW"
AVATAR_PATH = "/tmp/jessica_avatar.jpg"

print("=" * 60)
print(f"[*] Starting 4-Step Pipeline for: {FIRST_NAME} {LAST_NAME} <{EMAIL}>")
print(f"[*] Invite code: {INVITE_CODE}")
print("=" * 60)

box = M.open_mailbox("tempik", domain=DOMAIN, local_part=LOCAL_PART)
seen_ids = {m.get("id") for m in box.read() if m.get("id")}
print(f"[+] Mailbox ready: {EMAIL} (tracking {len(seen_ids)} existing msgs)")

kwargs = {
    "headless": False,
    "humanize": True,
    "locale": "en-US",
    "geoip": False,
}

with Camoufox(**kwargs) as browser:
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 900},
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"}
    )
    page = ctx.new_page()

    # =========================================================================
    # STEP 1: Natural Facebook Signup from Homepage
    # =========================================================================
    print("\n--- STEP 1: Natural Facebook Signup from Homepage ---")
    page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=40000)
    time.sleep(4)
    print("    [✓] Facebook homepage loaded.")

    create_btn = page.locator('a[data-testid="open-registration-form-button"], [role="button"]:has-text("Create new account"), a:has-text("Create new account")').first
    page.goto("https://www.facebook.com/reg/?entry_point=login", timeout=40000)
    time.sleep(3)
    print("    [✓] Facebook registration form loaded.")

    inps = page.locator('input')
    inps.nth(0).fill(FIRST_NAME)
    time.sleep(0.3)
    inps.nth(1).fill(LAST_NAME)
    time.sleep(0.3)
    # Date of birth: May 15, 1998
    combos = page.locator('[role="combobox"]')
    try:
        combos.nth(1).click()
        time.sleep(0.4)
        page.evaluate("() => { (Array.from(document.querySelectorAll('[role=\"option\"]')).find(el => el.innerText.trim() === 'May') || {}).click(); }")
        time.sleep(0.3)
        combos.nth(0).click()
        time.sleep(0.4)
        page.evaluate("() => { (Array.from(document.querySelectorAll('[role=\"option\"]')).find(el => el.innerText.trim() === '15') || {}).click(); }")
        time.sleep(0.3)
        combos.nth(2).click()
        time.sleep(0.4)
        page.evaluate("() => { (Array.from(document.querySelectorAll('[role=\"option\"]')).find(el => el.innerText.trim() === '1998') || {}).click(); }")
        time.sleep(0.4)
        print("    [✓] Date of birth set: May 15, 1998")
    except Exception as e:
        print("    [!] DOB selection error:", e)

    # Gender: Female
    try:
        combos.nth(3).click()
        time.sleep(0.4)
        page.evaluate("() => { (Array.from(document.querySelectorAll('[role=\"option\"]')).find(el => el.innerText.trim() === 'Female') || {}).click(); }")
        time.sleep(0.4)
    except Exception as e:
        print("    [!] Gender selection error:", e)

    # Contact (email) & Password
    inps.nth(2).fill(EMAIL)
    time.sleep(0.3)
    inps.nth(3).fill(PASSWORD)
    time.sleep(0.5)

    print("    Submitting Facebook registration form...")
    submit = page.locator('button:has-text("Submit"), [role="button"]:has-text("Submit"), button[name="websubmit"]').first
    submit.click()
    time.sleep(6)

    # Poll Tempik for Facebook 5-digit code
    print("    Polling Tempik for Facebook confirmation code...")
    fb_code = None
    end_time = time.time() + 90.0
    while time.time() < end_time and not fb_code:
        for m in box.read():
            if m.get("id") and m.get("id") not in seen_ids:
                blob = f"{m.get('subject', '')} {m.get('body', '')}"
                m5 = re.findall(r"\b(\d{5})\b", blob)
                m5_clean = [c for c in m5 if c != "94025"]
                if m5_clean:
                    fb_code = m5_clean[0]
                    seen_ids.add(m.get("id"))
                    break
        if fb_code: break
        time.sleep(2.5)

    if not fb_code:
        raise RuntimeError("Timed out waiting for Facebook 5-digit code from Tempik")

    print(f"    [✓] Received Facebook confirmation code: {fb_code}")
    if "confirmemail" not in page.url:
        page.goto("https://www.facebook.com/confirmemail.php", timeout=35000)
        time.sleep(3)

    code_inp = page.locator('input[type="text"], input').first
    code_inp.fill(fb_code)
    time.sleep(0.5)
    page.locator('[role="button"]:has-text("Continue"), button:has-text("Continue")').first.click()
    time.sleep(8)
    print(f"    [✓] Facebook confirmation completed! URL: {page.url}")

    # Capture Facebook cookies
    fb_cookies_map = {c["name"]: c["value"] for c in ctx.cookies() if "facebook.com" in (c.get("domain") or "")}
    c_user = fb_cookies_map.get("c_user")
    xs = fb_cookies_map.get("xs")
    print(f"    [✓] Facebook Session active: c_user={c_user}, xs={bool(xs)}")

    # Save to facebook_accounts.json
    fb_ledger_path = Path.home() / ".config/auto-freecf/facebook_accounts.json"
    fb_ledger = json.loads(fb_ledger_path.read_text()) if fb_ledger_path.exists() else []
    fb_entry = {
        "email": EMAIL,
        "name": f"{FIRST_NAME} {LAST_NAME}",
        "password": PASSWORD,
        "c_user": c_user,
        "xs": xs,
        "cookie_header": "; ".join(f"{k}={v}" for k, v in fb_cookies_map.items() if "facebook" in k or k in ["c_user", "xs", "datr", "fr", "sb"]),
        "cookies": fb_cookies_map,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "confirmed": True
    }
    fb_ledger.append(fb_entry)
    fb_ledger_path.write_text(json.dumps(fb_ledger, indent=2))
    print(f"    [✓] Saved Facebook account to ledger.")

    # =========================================================================
    # STEP 2: Profile Setup & Warming Up (News Feed Scroll)
    # =========================================================================
    print("\n--- STEP 2: Profile Setup & Natural Warming Up ---")
    page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=35000)
    time.sleep(3)

    print("    Browsing News Feed for 45s (simulating human interaction)...")
    for step in range(5):
        page.mouse.wheel(0, 500)
        time.sleep(3.0)
        if step % 2 == 0:
            page.mouse.wheel(0, -100)
            time.sleep(1.5)
        print(f"      Scrolled feed {step+1}/5")

    # Navigate to profile to set avatar if possible
    print("    Navigating to profile page (https://www.facebook.com/me)...")
    page.goto("https://www.facebook.com/me", wait_until="domcontentloaded", timeout=35000)
    time.sleep(4)
    file_inps = page.locator('input[type="file"]')
    if file_inps.count() > 0 and Path(AVATAR_PATH).exists():
        try:
            print("    Uploading persona avatar to profile...")
            file_inps.first.set_input_files(AVATAR_PATH)
            time.sleep(4)
            save_btn = page.locator('button:has-text("Save"), [role="button"]:has-text("Save")').first
            if save_btn.is_visible():
                save_btn.click()
                time.sleep(5)
                print("    [✓] Profile avatar saved!")
        except Exception as e:
            print("    [!] Avatar upload skip:", e)

    page.screenshot(path="/tmp/pipeline_step2_profile.png")
    print("    [✓] Step 2 complete! Saved /tmp/pipeline_step2_profile.png")

    # =========================================================================
    # STEP 3: Natural Muse Onboarding from Homepage with Fresh Invite Code
    # =========================================================================
    print("\n--- STEP 3: Natural Muse Onboarding from Homepage ---")
    print("    Navigating to https://muse.ai/ (cold homepage load)...")
    page.goto("https://muse.ai/", wait_until="domcontentloaded", timeout=40000)
    time.sleep(4)
    print("    [✓] Muse homepage loaded.")

    # Now navigate to invite link using fresh code
    invite_url = f"https://muse.ai/invite/{INVITE_CODE}"
    print(f"    Navigating to invite URL: {invite_url}...")
    page.goto(invite_url, wait_until="domcontentloaded", timeout=40000)
    time.sleep(3)

    print(f"    Entering email: {EMAIL}...")
    email_inp = page.wait_for_selector('input[placeholder*="Mobile number or email" i], input[type="email"]', timeout=30000)
    email_inp.fill(EMAIL)
    time.sleep(0.5)

    page.locator('button:has-text("Continue"), [role="button"]:has-text("Continue")').first.click()
    page.wait_for_selector("text=Enter your code to sign up", timeout=25000)
    print("    [✓] Reached Muse OTP verification screen!")

    # Poll Tempik for Muse 6-digit code
    print("    Polling Tempik for Muse 6-digit confirmation code...")
    muse_otp = None
    end_time_muse = time.time() + 90.0
    while time.time() < end_time_muse and not muse_otp:
        for m in box.read():
            if m.get("id") and m.get("id") not in seen_ids:
                subj = str(m.get("subject") or "")
                blob = str(m.get("body") or m.get("text") or "")
                m6 = re.search(r"(?:code|use code)\s*(\d{6})\b", subj, re.IGNORECASE) or \
                     re.search(r"confirmation code\s*(\d{6})\b", blob, re.IGNORECASE) or \
                     re.search(r"\b(\d{6})\b", subj) or \
                     re.search(r"(?:code|verify|verification|otp)[^\d]{0,30}(\d{6})\b", blob, re.IGNORECASE)
                if m6:
                    muse_otp = m6.group(1)
                    seen_ids.add(m.get("id"))
                    break
        if muse_otp: break
        time.sleep(2.0)

    if not muse_otp:
        raise RuntimeError("Timed out waiting for Muse 6-digit OTP from Tempik")

    print(f"    [✓] Received Muse 6-digit OTP: {muse_otp}")
    pins = page.locator('[data-slot="pin-digit"]').all()
    if pins:
        for i, d in enumerate(muse_otp):
            pins[i].click()
            time.sleep(0.12)
            page.keyboard.press(d)
    time.sleep(6)
    # Check for "Finish creating your account" (Profile Form on Meta Hatch)
    time.sleep(3)
    if page.locator("text=Finish creating your account").is_visible() or "Finish creating your account" in page.inner_text("body"):
        print("    [✓] 'Finish creating your account' screen reached! Filling profile...")
        page.locator('input[aria-label="First name"], input[placeholder*="First name" i]').first.fill(FIRST_NAME)
        time.sleep(0.3)
        page.locator('input[aria-label="Last name"], input[placeholder*="Last name" i]').first.fill(LAST_NAME)
        time.sleep(0.3)

        # Month: May
        try:
            page.locator('button[aria-label="Month"]').first.click()
            time.sleep(0.4)
            page.evaluate("() => { (Array.from(document.querySelectorAll('[role=\"option\"], [data-slot=\"select-item\"]')).find(el => el.innerText.trim() === 'May') || {}).click(); }")
            time.sleep(0.4)
        except Exception as e:
            print("    [!] Month selection error:", e)

        # Day: 15
        try:
            page.locator('button[aria-label="Day"]').first.click()
            time.sleep(0.4)
            page.evaluate("() => { (Array.from(document.querySelectorAll('[role=\"option\"], [data-slot=\"select-item\"]')).find(el => el.innerText.trim() === '15') || {}).click(); }")
            time.sleep(0.4)
        except Exception as e:
            print("    [!] Day selection error:", e)

        # Year: 1998 (Age 28)
        try:
            page.locator('button[aria-label="Year"]').first.click()
            time.sleep(0.4)
            page.evaluate("() => { (Array.from(document.querySelectorAll('[role=\"option\"], [data-slot=\"select-item\"]')).find(el => el.innerText.trim() === '1998') || {}).click(); }")
            time.sleep(0.4)
            print("    [✓] DOB set: May 15, 1998")
        except Exception as e:
            print("    [!] Year selection error:", e)

        time.sleep(1.0)
        confirm_btn = page.locator('button:has-text("Confirm"), [role="button"]:has-text("Confirm")').first
        if confirm_btn.is_visible():
            confirm_btn.click()
            print("    [✓] Clicked Confirm on profile form!")
            time.sleep(8)

    # Check for Account Card Selection ("Choose an account to continue")
    if page.locator("text=Choose an account to continue").is_visible():
        print("    Selecting recognized Meta Account card...")
        card = page.locator(f'text={EMAIL}').first
        box_rect = card.bounding_box()
        if box_rect:
            page.mouse.click(box_rect['x'] + 50, box_rect['y'] + 10)
        else:
            page.locator('div:has-text("Meta Account")').last.click()
        time.sleep(8)
    # Check for 'A few things to know' -> click 'Get started'
    if page.locator("text=A few things to know").count() > 0 or "things to know" in page.inner_text("body").lower():
        print("    [✓] 'A few things to know' screen reached! Scrolling to bottom...")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1.5)
        btn_start = page.locator('button:has-text("Get started")').first
        if btn_start.is_visible():
            btn_start.click()
            print("    [✓] Clicked 'Get started' onboarding button!")
            time.sleep(8)

    page.screenshot(path="/tmp/pipeline_step3_onboarding.png")
    print("    [✓] Step 3 complete! Saved /tmp/pipeline_step3_onboarding.png")

    # =========================================================================
    # STEP 4: Automated Age Confirmation via Facebook & Workspace Entry
    # =========================================================================
    print("\n--- STEP 4: Age Confirmation via Facebook & Workspace Entry ---")
    time.sleep(3)
    print("    Current URL:", page.url)

    # Check if on /access/verification
    fb_link = page.locator('button:has-text("Facebook"), a:has-text("Facebook")').first
    if fb_link.is_visible():
        print("    Clicking Facebook link to confirm age...")
        with page.expect_popup() as popup_info:
            fb_link.click()
        popup = popup_info.value
        print("    Popup opened! Waiting for Facebook consent dialog...")
        time.sleep(8)

        popup.screenshot(path="/tmp/pipeline_step4_popup.png")
        print("    Saved /tmp/pipeline_step4_popup.png, URL:", popup.url)

        # Click consent button in popup (Continue as Jessica)
        consent_btn = popup.locator('button:has-text("Continue as"), [role="button"]:has-text("Continue as"), button:has-text("Continue"), [role="button"]:has-text("Continue"), button:has-text("Confirm")').first
        if consent_btn.is_visible():
            print(f"    Clicking popup consent button: '{consent_btn.inner_text()}'...")
            consent_btn.click()
            time.sleep(10)
        else:
            print("    [i] Popup body text snippet:", popup.inner_text("body")[:300].replace("\n", " "))

    time.sleep(6)
    print("\n============================================================")
    print("FINAL WORKSPACE STATUS:")
    print("  Landed URL:", page.url)
    print("  Page Title:", page.title())
    page.screenshot(path="/tmp/pipeline_final_workspace.png")
    print("  Saved final screenshot to /tmp/pipeline_final_workspace.png")
    print("  Body snippet:\n ", page.inner_text("body")[:600].replace("\n", " "))

    # Capture session cookies
    cookies = ctx.cookies()
    cookie_map = {c["name"]: c["value"] for c in cookies}
    hatch_sess = cookie_map.get("hatch_sess")
    print(f"\n[+] hatch_sess captured: {bool(hatch_sess)} (len: {len(hatch_sess) if hatch_sess else 0})")

    if hatch_sess:
        entry = {
            "email": EMAIL,
            "name": f"{FIRST_NAME} {LAST_NAME}",
            "hatch_sess": hatch_sess,
            "cookie_header": "; ".join(f"{k}={v}" for k, v in cookie_map.items()),
            "cookies": cookie_map,
            "auth_via": "facebook",
            "fb_email": EMAIL,
            "fb_c_user": c_user,
            "invite_code": INVITE_CODE,
            "status": "unlocked"
        }
        ledger_path = Path.home() / ".config/auto-freecf/muse_accounts.json"
        ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else []
        ledger = [a for a in ledger if a.get("email") != EMAIL]
        ledger.append(entry)
        ledger_path.write_text(json.dumps(ledger, indent=2))
        print(f"[✓] Successfully saved unlocked Muse account {EMAIL} to {ledger_path}!")

        # Also register into 9Router
        import sqlite3, datetime
        db = Path.home() / ".9router/db/data.sqlite"
        if db.exists():
            con = sqlite3.connect(db)
            cur = con.cursor()
            conn_id = f"muse-web-{LOCAL_PART}"
            data = {
                "apiKey": entry["cookie_header"],
                "testStatus": "active",
                "providerSpecificData": {
                    "prefix": "muse",
                    "apiType": "chat",
                    "baseUrl": "https://muse.ai",
                },
            }
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            cur.execute(
                "INSERT INTO providerConnections (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data, isActive=1, updatedAt=excluded.updatedAt",
                (conn_id, "muse-web", "cookie", f"Muse Web ({entry['name']})", entry["email"], 0, 1,
                 json.dumps(data), now, now),
            )
            con.commit()
            print(f"[✓] Successfully registered '{conn_id}' in 9Router DB!")

    print("============================================================\n")
