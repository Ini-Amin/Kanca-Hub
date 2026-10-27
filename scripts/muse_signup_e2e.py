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
FB_LEDGER_PATH = Path.home() / ".config/auto-freecf/facebook_accounts.json"
NINE_ROUTER_DB = Path.home() / ".9router/db/data.sqlite"


def _fb_context_cookies(fb: dict) -> list[dict]:
    """Saved Facebook session cookies, re-scoped to .facebook.com for Muse's Meta login."""
    http_only = {"xs", "c_user", "datr", "fr", "sb", "presence", "ddid"}
    out = []
    for name, value in (fb.get("cookies") or {}).items():
        out.append({
            "name": name, "value": value, "domain": ".facebook.com", "path": "/",
            "secure": True, "httpOnly": name in http_only, "sameSite": "None",
        })
    return out


def _wait_otp(box, timeout: float = 100.0) -> str | None:
    """Muse sends 'Confirmation code NNNNNN' from Meta; the preheader carries a decoy 6-digit."""
    end = time.time() + timeout
    while time.time() < end:
        for m in reversed(box.read()):
            blob = " ".join(str(m.get(k) or "") for k in ("subject", "text", "body"))
            hit = re.search(r"Confirmation code\s*(\d{6})", blob, re.IGNORECASE)
            if not hit:
                hit = re.search(r"(?:code|verify|verification|otp)[^\d]{0,30}(\d{6})\b", blob, re.IGNORECASE)
            if hit:
                return hit.group(1)
        time.sleep(2.5)
    return None


def _stage(page, tag: str) -> tuple[str, list[str]]:
    try:
        body = re.sub(r"\s+", " ", page.inner_text("body"))[:500]
    except Exception:
        body = ""
    try:
        pels = page.eval_on_selector_all(
            "[data-pel-click]", "els => els.map(e => e.getAttribute('data-pel-click'))")
    except Exception:
        pels = []
    print(f"      [{tag}] url={page.url}")
    print(f"      [{tag}] pels={pels}")
    print(f"      [{tag}] body: {body}")
    return body, pels


def _upsert_9router(acc: dict) -> None:
    if not NINE_ROUTER_DB.exists():
        return
    import sqlite3
    now = datetime.now(timezone.utc).isoformat()
    conn_id = f"muse-web-{acc['email'].split('@')[0]}"
    data = json.dumps({
        "apiKey": acc["cookie_header"],
        "testStatus": "active",
        "providerSpecificData": {
            "authMethod": "cookie",
            "connectionProxyEnabled": False,
            "connectionProxyUrl": "",
            "connectionNoProxy": "",
            "baseUrl": "https://muse.ai",
        },
    })
    con = sqlite3.connect(NINE_ROUTER_DB)
    cur = con.cursor()
    row = cur.execute("SELECT id FROM providerConnections WHERE id = ?", (conn_id,)).fetchone()
    if row:
        cur.execute("UPDATE providerConnections SET data = ?, isActive = 1, updatedAt = ? WHERE id = ?",
                    (data, now, conn_id))
    else:
        cur.execute(
            "INSERT INTO providerConnections (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (conn_id, "muse-web", "cookie", f"Muse Web ({acc['name']})", acc["email"], 0, 1, data, now, now))
    con.commit()
    con.close()
    print(f"      [✓] 9Router connection '{conn_id}' upserted")


def run_muse_via_facebook(headless: bool = True, fb_index: int = -1) -> dict:
    """Muse E2E seeded with an existing Facebook session.

    Muse's only door is a Meta account identified by email/phone OTP, so the
    Facebook account we already own supplies both halves: its email for the
    Meta identity and its session cookies so any Accounts Center / FB link
    step auto-approves instead of dead-ending on age verification.
    """
    fb_ledger = json.loads(FB_LEDGER_PATH.read_text())
    fb = fb_ledger[fb_index]
    email = fb["email"]
    name = fb.get("name") or "Muse User"
    first_name, last_name = (name.split()[0], name.split()[-1]) if " " in name else (name, "Kanca")
    print(f"\n[*] Facebook seed: {email} ({name})")

    local_part = email.split("@")[0]
    box = M.open_mailbox("tempik", domain="kancalabs.my.id", local_part=local_part)
    print(f"    [✓] Mailbox attached: {box.address}")

    with Camoufox(headless=headless, humanize=True) as browser:
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        ctx.add_cookies(_fb_context_cookies(fb))
        page = ctx.new_page()

        print("[1/6] muse.ai login with the Facebook account email…")
        page.goto("https://muse.ai/", timeout=60000)
        time.sleep(3)
        page.click('[data-pel-click="logged_out_login_click"]')
        inp = page.wait_for_selector('input[placeholder*="Mobile number or email"]', timeout=30000)
        inp.fill(email)
        time.sleep(0.6)
        page.click('[data-pel-click="login_contact_continue_click"]')
        time.sleep(6)
        body, pels = _stage(page, "after-email")

        print("[2/6] Answering the Meta challenge…")
        if "pin-digit" in page.content() or "code to sign" in body.lower() or "Enter your code" in body:
            otp = _wait_otp(box)
            if not otp:
                raise RuntimeError("Muse OTP never arrived in the Tempik inbox")
            print(f"      [✓] OTP {otp}")
            pins = page.locator('[data-slot="pin-digit"]').all()
            for i, d in enumerate(otp):
                pins[i].click()
                time.sleep(0.15)
                page.keyboard.press(d)
                time.sleep(0.1)
            time.sleep(4)
            _stage(page, "after-otp")
        elif page.locator('input[type="password"]').count():
            print("      [i] Password challenge (existing Meta account) — using the Facebook password")
            page.locator('input[type="password"]').first.fill(fb.get("password", ""))
            page.keyboard.press("Enter")
            time.sleep(8)
            _stage(page, "after-password")

        print("[3/6] Profile / onboarding…")
        for _ in range(6):
            body, pels = _stage(page, "step")
            if "Finish creating your account" in body:
                page.evaluate(
                    """(d) => {
                    const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
                    const fn = document.querySelector('input[placeholder="First name"]');
                    const ln = document.querySelector('input[placeholder="Last name"]');
                    if (fn) { set.call(fn, d.first); fn.dispatchEvent(new Event('input', {bubbles:true})); }
                    if (ln) { set.call(ln, d.last); ln.dispatchEvent(new Event('input', {bubbles:true})); }
                    const y = Array.from(document.querySelectorAll('select')).find(s =>
                        Array.from(s.options).some(o => o.value === '1998'));
                    if (y) { y.value = '1998'; y.dispatchEvent(new Event('change', {bubbles:true})); }
                }""", {"first": first_name, "last": last_name})
                time.sleep(1)
                try:
                    page.click('button[data-slot="button"]:has-text("Confirm")', timeout=5000)
                except Exception:
                    pass
                time.sleep(5)
                continue
            fb_link = next((p for p in pels if p and "link" in p.lower()
                            and ("fb" in p.lower() or "facebook" in p.lower())), None)
            if fb_link:
                print(f"      [→] clicking Muse link step: {fb_link}")
                page.click(f'[data-pel-click="{fb_link}"]')
                time.sleep(8)
                for pg in ctx.pages:
                    if "facebook.com" in pg.url or "accounts" in pg.url:
                        for label in ("Continue", "Allow", "Yes, finish setup", "Confirm"):
                            try:
                                pg.get_by_role("button", name=label).first.click(timeout=4000)
                                print(f"      [→] {label} on {pg.url.split('?')[0]}")
                                time.sleep(4)
                                break
                            except Exception:
                                continue
                continue
            if ctx.cookies() and any(c["name"] == "hatch_sess" for c in ctx.cookies()):
                break
            time.sleep(4)

        print("[4/6] Waiting for hatch_sess…")
        end = time.time() + 90
        while time.time() < end:
            if any(c["name"] == "hatch_sess" for c in ctx.cookies()):
                break
            time.sleep(3)
        _stage(page, "final")

        cookie_map = {c["name"]: c["value"] for c in ctx.cookies()}
        hatch = cookie_map.get("hatch_sess")
        if not hatch:
            raise RuntimeError(f"no hatch_sess captured; cookies={sorted(cookie_map)} url={page.url}")

        entry = {
            "email": email,
            "name": name,
            "hatch_sess": hatch,
            "cookie_header": "; ".join(f"{k}={v}" for k, v in cookie_map.items()),
            "cookies": cookie_map,
            "seed": "facebook",
            "fb_email": email,
            "status": "facebook_linked",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        import fcntl
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER_PATH.with_suffix(".lock"), "w") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            ledger = json.loads(LEDGER_PATH.read_text()) if LEDGER_PATH.exists() else []
            ledger = [a for a in ledger if a.get("email") != email]
            ledger.append(entry)
            tmp = LEDGER_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(ledger, indent=2))
            tmp.replace(LEDGER_PATH)
            fcntl.flock(lf, fcntl.LOCK_UN)

        print(f"\n[✓] MUSE VIA FACEBOOK OK — {email}")
        print(f"    hatch_sess: {hatch[:32]}…")
        print(f"    ledger: {LEDGER_PATH}")
        _upsert_9router(entry)
        return entry
FB_LEDGER_PATH = Path.home() / ".config/auto-freecf/facebook_accounts.json"


def _load_fb_account(fb_email: str) -> dict:
    """Pick a Facebook account from the shared ledger (explicit email or first entry)."""
    accounts = json.loads(FB_LEDGER_PATH.read_text())
    if fb_email:
        for a in accounts:
            if a["email"].lower() == fb_email.lower():
                return a
        raise RuntimeError(f"Facebook account {fb_email} not in {FB_LEDGER_PATH}")
    if not accounts:
        raise RuntimeError(f"No Facebook accounts in {FB_LEDGER_PATH}")
    return accounts[0]


def _fb_cookies_for_context(fb_acc: dict) -> list[dict]:
    out = []
    for name, value in fb_acc.get("cookies", {}).items():
        out.append({
            "name": name, "value": value, "domain": ".facebook.com",
            "path": "/", "secure": True, "httpOnly": name in ("xs", "fr"),
        })
    return out


def _persist_fb_cookies(fb_acc: dict, context) -> None:
    """Write refreshed .facebook.com cookies back to the Facebook ledger."""
    fb_cookies = {c["name"]: c["value"] for c in context.cookies() if "facebook.com" in (c.get("domain") or "")}
    if not fb_cookies:
        return
    accounts = json.loads(FB_LEDGER_PATH.read_text())
    for a in accounts:
        if a["email"] == fb_acc["email"]:
            a["cookies"] = {**a.get("cookies", {}), **fb_cookies}
            a["cookie_header"] = "; ".join(f"{k}={v}" for k, v in a["cookies"].items())
            if "c_user" in fb_cookies:
                a["c_user"] = fb_cookies["c_user"]
            if "xs" in fb_cookies:
                a["xs"] = fb_cookies["xs"]
            a["muse_email"] = fb_acc["email"]
            a["muse_linked_at"] = datetime.now(timezone.utc).isoformat()
            break
    FB_LEDGER_PATH.write_text(json.dumps(accounts, indent=2))
    print(f"      [✓] Facebook cookies refreshed & saved ({len(fb_cookies)} cookies)")


def _register_9router(entry: dict) -> None:
    """Upsert the muse-web provider connection so 9Router can use this session."""
    import sqlite3
    db = Path.home() / ".9router/db/data.sqlite"
    if not db.exists():
        return
    local = entry["email"].split("@")[0]
    conn_id = f"muse-web-{local}"
    data = {
        "apiKey": entry["cookie_header"],
        "testStatus": "active",
        "providerSpecificData": {
            "authMethod": "cookie",
            "connectionProxyEnabled": False,
            "connectionProxyUrl": "",
            "connectionNoProxy": "",
            "baseUrl": "https://muse.ai",
        },
    }
    now = datetime.now(timezone.utc).isoformat()
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO providerConnections (id, provider, authType, name, email, priority, isActive, data, createdAt, updatedAt) "
        "VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET data=excluded.data, isActive=1, updatedAt=excluded.updatedAt",
        (conn_id, "muse-web", "cookie", f"Muse Web ({entry['name']})", entry["email"], 0, 1,
         json.dumps(data), now, now),
    )
    con.commit()
    print(f"      [✓] 9Router connection '{conn_id}' registered (provider muse-web)")


def run_muse_signup(headless: bool = True, password: str = "", local_part: str = "", country: str = "",
                    fb_email: str = "") -> dict:
    fb_acc = None
    if fb_email or os.environ.get("MUSE_VIA_FACEBOOK"):
        fb_acc = _load_fb_account(fb_email or os.environ.get("MUSE_VIA_FACEBOOK", ""))
        email = fb_acc["email"]
        local_part = email.split("@")[0]
        name = fb_acc.get("name") or persona.full_name()
        print(f"\n[0/6] Facebook identity: {email} (c_user={fb_acc.get('c_user')})")
    else:
        email = ""
        name = persona.full_name()

    parts = name.split()
    first_name = parts[0]
    last_name = parts[-1] if len(parts) > 1 else "Kanca"

    password = password or os.environ.get("MUSE_PASSWORD") or (persona.password() if hasattr(persona, "password") else "MuseTmp!2026xQ")

    if fb_acc:
        box = M.open_mailbox("tempik", domain="kancalabs.my.id", local_part=local_part)
        print(f"      [✓] Reusing FB mailbox: {box.address} (User: {first_name} {last_name})")
    else:
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
                    # Try US East instead of Central (Central is slow)
                    us_east = [h for k, v in hosts.items() if "us east" in k.lower() for h in v[:1]]
                    if us_east:
                        host = us_east[0]
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
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        if fb_acc:
            context.add_cookies(_fb_cookies_for_context(fb_acc))
            print(f"      [✓] Injected {len(fb_acc.get('cookies', {}))} Facebook cookies into context")
        page = context.new_page()

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

        print("[5/6] Submitting OTP...")
        time.sleep(1.0)
        pins = page.locator('[data-slot="pin-digit"]').all()
        if pins:
            for i, d in enumerate(otp):
                pins[i].click()
                time.sleep(0.2)
                page.keyboard.press(d)
                time.sleep(0.1)
        else:
            # Single-input variant: <input autocomplete="one-time-code"> + Confirm
            otp_inp = page.wait_for_selector('input[autocomplete="one-time-code"], input[name*="code" i]', timeout=10000, state="attached")
            otp_inp.fill(otp)
            time.sleep(0.5)
            remember = page.query_selector('input[type="checkbox"]')
            if remember and not remember.is_checked():
                remember.check()
                print("      [✓] 'Remember this device' checked")
            page.click('button:has-text("Confirm")', timeout=8000)
            time.sleep(5)

        # Profile screen only appears for a brand-new Meta identity
        try:
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
        except Exception as e:
            print(f"      [!] No profile screen ({e.__class__.__name__}) — identity already exists, continuing")

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
            "auth_via": "facebook" if fb_acc else "email_otp",
        }
        if fb_acc:
            account_entry["fb_email"] = fb_acc["email"]
            account_entry["fb_c_user"] = fb_acc.get("c_user")
            _persist_fb_cookies(fb_acc, context)

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
        _register_9router(account_entry)
        return account_entry


def main():
    parser = argparse.ArgumentParser(description="Muse.ai Automated Signup with Domain Email")
    parser.add_argument("--count", "-n", type=int, default=1, help="Number of accounts to create (default: 1)")
    parser.add_argument("--headed", action="store_true", help="Run with visible browser window")
    parser.add_argument("--password", default="", help="Override password (default: persona/env MUSE_PASSWORD)")
    parser.add_argument("--local-part", default="", help="Mailbox local part (default: random/env MUSE_LOCAL_PART)")
    parser.add_argument("--country", default="", help="US/CA residential proxy country (default: direct)")
    parser.add_argument("--fb-email", default="", help="Sign in to Muse with an existing Facebook account (from facebook_accounts.json); use 'auto' for the first unused one")
    args = parser.parse_args()

    fb_email = args.fb_email
    if fb_email == "auto":
        muse_emails = {a["email"] for a in json.loads(LEDGER_PATH.read_text())} if LEDGER_PATH.exists() else set()
        free = [a["email"] for a in json.loads(FB_LEDGER_PATH.read_text()) if a["email"] not in muse_emails]
        if not free:
            sys.exit("No unused Facebook account left in the ledger")
        fb_email = free[0]
        print(f"[auto] Facebook account selected: {fb_email}")

    for i in range(args.count):
        print(f"\n=== Creating Muse.ai Account {i+1} of {args.count} ===")
        try:
            run_muse_signup(headless=not args.headed, password=args.password, local_part=args.local_part,
                            country=args.country, fb_email=fb_email)
            if i + 1 < args.count:
                time.sleep(3)
        except Exception as e:
            print(f"[!] Error creating account {i+1}: {e}")


if __name__ == "__main__":
    main()
