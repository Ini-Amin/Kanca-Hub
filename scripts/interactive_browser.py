#!/usr/bin/env python3
"""scripts/interactive_browser.py — Headed Anti-detect Browser for Interactive User Sign-up.

Opens a live Camoufox window on display :0 routed through US New York Proxy,
injecting the authenticated Facebook session for Sarah Johnson.
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

# Ensure graphical display access
if not os.environ.get("XAUTHORITY"):
    xauth = list(Path("/run/user/1000").glob("xauth_*"))
    if xauth:
        os.environ["XAUTHORITY"] = str(xauth[0])
if not os.environ.get("DISPLAY"):
    os.environ["DISPLAY"] = ":0"

import scripts.mailboxes as M
import scripts.windscribe_free_proxy as ws
from camoufox.sync_api import Camoufox

MAILBOX = "karangpuspa81"
DOMAIN = "kancalabs.my.id"
EMAIL = f"{MAILBOX}@{DOMAIN}"

print("=" * 60)
print(f"[*] Starting Interactive Browser for: {EMAIL}")

# Use fresh Windscribe US East (New York City)
creds = ws.get_credentials()
host = "us-east-158.totallyacdn.com"
proxy = {
    "server": f"https://{host}:443",
    "username": creds["username"],
    "password": creds["password"],
}
print(f"[+] Proxy Endpoint : {host} (Windscribe US East, NYC)")

box = M.open_mailbox("tempik", domain=DOMAIN, local_part=MAILBOX)
seen_ids = {m.get("id") for m in box.read() if m.get("id")}
print(f"[+] Mailbox active: {EMAIL} (tracking {len(seen_ids)} existing msgs)")

# Load Facebook cookies from ledger
fb_ledger_path = Path.home() / ".config/auto-freecf/facebook_accounts.json"
fb_cookies = []
if fb_ledger_path.exists():
    try:
        accs = json.loads(fb_ledger_path.read_text())
        target = next((a for a in accs if a.get("email") == EMAIL), None)
        if target and target.get("cookies"):
            for k, v in target["cookies"].items():
                fb_cookies.append({
                    "name": k, "value": v, "domain": ".facebook.com",
                    "path": "/", "secure": True
                })
            print(f"[✓] Loaded {len(fb_cookies)} saved Facebook cookies for {EMAIL} (c_user={target.get('c_user')})!")
    except Exception as e:
        print("[!] Error loading cookies:", e)

kwargs = {
    "headless": False,
    "humanize": True,
    "locale": "en-US",
    "geoip": False,
    "proxy": proxy,
}

print("[+] Launching Camoufox headed on your screen...")
with Camoufox(**kwargs) as browser:
    ctx = browser.new_context(
        viewport={"width": 1280, "height": 850},
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"}
    )
    if fb_cookies:
        ctx.add_cookies(fb_cookies)
        print("[+] Injected Facebook cookies into browser context.")

    # Tab 1: Facebook (to complete human confirmation)
    page_fb = ctx.new_page()
    page_fb.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=45000)
    print("[+] Tab 1 opened: Facebook (https://www.facebook.com/)")

    # Tab 2: Muse Invite
    page_muse = ctx.new_page()
    page_muse.goto("https://muse.ai/invite/H9BZ8A", wait_until="domcontentloaded", timeout=45000)
    print("[+] Tab 2 opened: Muse Invite H9BZ8A (https://muse.ai/invite/H9BZ8A)")

    print("\n" + "=" * 60)
    print(">>> BROWSER IS NOW OPEN ON YOUR SCREEN! <<<")
    print(f"1. Tab 1: Facebook (selesaikan konfirmasi manusia jika ada teks captcha).")
    print(f"2. Tab 2: Muse Invite (H9BZ8A) -> Masukkan email: {EMAIL}")
    print("Script ini terus memantau inbox Tempik dan akan menampilkan OTP jika ada.")
    print("=" * 60 + "\n")

    # Keep browser alive and monitor inbox
    last_cookie_save = time.time()
    while True:
        alive_pages = [p for p in ctx.pages if not p.is_closed()]
        if not alive_pages:
            print("[*] All browser windows closed by user. Exiting...")
            break

        # Check for new OTP messages in Tempik
        try:
            msgs = box.read()
            for m in msgs:
                mid = m.get("id")
                if mid and mid not in seen_ids:
                    seen_ids.add(mid)
                    subj = str(m.get("subject") or "")
                    body = str(m.get("body") or m.get("text") or "")
                    print(f"\n[🔔 NEW EMAIL RECEIVED] Subject: {subj}")
                    
                    m5 = re.findall(r"\b(\d{5})\b", f"{subj} {body}")
                    m6 = re.findall(r"\b(\d{6})\b", f"{subj} {body}")
                    m5_clean = [c for c in m5 if c != "94025"]
                    if m5_clean:
                        print(f"👉 KODE OTP FACEBOOK (5 DIGIT): {m5_clean[0]}")
                    if m6:
                        print(f"👉 KODE OTP META / MUSE (6 DIGIT): {m6[0]}")
        except Exception:
            pass

        # Periodically save any captured cookies
        if time.time() - last_cookie_save > 15:
            last_cookie_save = time.time()
            cookies = ctx.cookies()
            cookie_map = {c["name"]: c["value"] for c in cookies}
            
            # Save Facebook cookies
            if "c_user" in cookie_map and "xs" in cookie_map:
                try:
                    ledger = json.loads(fb_ledger_path.read_text()) if fb_ledger_path.exists() else []
                    for a in ledger:
                        if a.get("email") == EMAIL:
                            a["cookies"] = {k: v for k, v in cookie_map.items() if "facebook" in k or k in ["c_user", "xs", "datr", "fr", "sb"]}
                            a["cookie_header"] = "; ".join(f"{k}={v}" for k, v in a["cookies"].items())
                            a["confirmed"] = True
                            fb_ledger_path.write_text(json.dumps(ledger, indent=2))
                            break
                except Exception:
                    pass

            # Save Muse cookies
            if "hatch_sess" in cookie_map:
                muse_ledger_path = Path.home() / ".config/auto-freecf/muse_accounts.json"
                try:
                    m_ledger = json.loads(muse_ledger_path.read_text()) if muse_ledger_path.exists() else []
                    if not any(a.get("email") == EMAIL for a in m_ledger):
                        m_entry = {
                            "email": EMAIL,
                            "name": "Sarah Johnson",
                            "hatch_sess": cookie_map.get("hatch_sess"),
                            "cookie_header": "; ".join(f"{k}={v}" for k, v in cookie_map.items()),
                            "cookies": cookie_map,
                            "auth_via": "manual_headed",
                            "invite_code": "H9BZ8A",
                            "egress_ip": "135.136.3.62",
                            "status": "active"
                        }
                        m_ledger.append(m_entry)
                        muse_ledger_path.write_text(json.dumps(m_ledger, indent=2))
                        print(f"\n[✓] CAPTURED & SAVED MUSE HATCH_SESS FOR {EMAIL}!")
                except Exception:
                    pass

        time.sleep(2)
