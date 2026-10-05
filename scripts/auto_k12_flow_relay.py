#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Auto ChatGPT K-12 Teacher Workspace Creator & Verifier  (KancaHub edition v2)

Pipeline upgrade over the original: instead of the flaky temp.tf service, this
uses KancaHub's OWN self-hosted mail relay (Supabase Edge Function fed by
Cloudflare Email Routing). That relay reliably receives and parses mail, so the
OpenAI OTP and verification links actually arrive.

Flow:
  1. Create a fresh address on our domain (kancalabs.biz.id / .my.id).
  2. ChatGPT signup/login with that address.
  3. Poll OUR relay for the 6-digit OTP (and any verification link).
  4. Complete the K-12 onboarding, detect the SheerID URL.
  5. Run K12Verifier (self-contained SheerID flow).
  6. Capture the ChatGPT OAuth session -> k12_sessions.json (for 9Router codex).

Run from the K-12 tool dir so `from script import K12Verifier` resolves:
    cd ~/petani-proxy/Farm-Acc-ChatGPT-K-12-Teachers/PyRuntime_64
    python3 /path/to/auto_k12_flow_relay.py

Env:
    KANCAHUB_ENV   path to .env with SUPABASE_URL / ... (default
                   ~/.config/auto-freecf/.env)
    K12_MAIL_API   mail_api base (default from signup config)
    K12_MAIL_KEY   x-api-key for the relay
    K12_DOMAINS    comma list (default kancalabs.biz.id,kancalabs.my.id)
    K12_SESSION_OUT  session file (default k12_sessions.json)
"""

import os
import re
import sys
import json
import time
import random
import string
import urllib.request
import urllib.error
from pathlib import Path

from DrissionPage import Chromium, ChromiumOptions

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

try:
    from script import K12Verifier
except ImportError:
    K12Verifier = None

import requests


# ───────────────────────────────────────────── config / mail relay

def _load_env(path: str | None = None) -> dict:
    env: dict = {}
    p = Path(path or os.environ.get("KANCAHUB_ENV",
                                    str(Path.home() / ".config" / "auto-freecf" / ".env")))
    if p.exists():
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


_ENV = _load_env()
SUPABASE_URL = _ENV.get("SUPABASE_URL", "").rstrip("/")
MAIL_KEY = _ENV.get("TMK_KEY", "")
if not MAIL_KEY:
    # fall back to the signup config's mail_api_key
    try:
        cfg = json.loads((Path.home() / "Auto-FreeCF" / "signup_from_scratch" / "config.json").read_text())
        MAIL_KEY = cfg.get("mail_api_key", "")
        if not SUPABASE_URL and cfg.get("mail_api"):
            m = re.match(r"(https://[^/]+)", cfg["mail_api"])
            SUPABASE_URL = m.group(1) if m else ""
    except Exception:
        pass

MAIL_BASE = f"{SUPABASE_URL}/functions/v1/temp-mail-api"
MAIL_NEW = f"{MAIL_BASE}/new_address"
MAIL_INBOX = f"{MAIL_BASE}/parsed_mails"

DOMAINS = [d.strip() for d in os.environ.get(
    "K12_DOMAINS", "kancalabs.biz.id,kancalabs.my.id").split(",") if d.strip()]

SESSION_OUT = os.environ.get("K12_SESSION_OUT", "k12_sessions.json")


def create_mailbox() -> dict:
    """Create a fresh address on our relay. Returns {email, jwt}."""
    dom = random.choice(DOMAINS)
    r = requests.post(MAIL_NEW, json={"domain": dom},
                      headers={"x-api-key": MAIL_KEY}, timeout=60)
    r.raise_for_status()
    d = r.json()
    return {"email": d["address"], "jwt": d["jwt"]}


def poll_inbox(jwt: str) -> list[dict]:
    r = requests.get(MAIL_INBOX, headers={"Authorization": f"Bearer {jwt}",
                                          "x-api-key": MAIL_KEY}, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else data.get("results", [])


def _mail_text(m: dict) -> str:
    return " ".join(str(m.get(k, "")) for k in ("subject", "text", "body", "html", "snippet", "from"))


def wait_for_otp(jwt: str, max_wait: int = 150, delay: int = 5) -> str | None:
    """Poll our relay for a 6-digit OpenAI/ChatGPT code."""
    print(f"      [mail] polling our relay for OTP (max {max_wait}s)…", flush=True)
    start = time.time()
    seen = set()
    while time.time() - start < max_wait:
        try:
            for m in poll_inbox(jwt):
                mid = m.get("id") or m.get("message_id")
                if mid in seen:
                    continue
                blob = re.sub(r"<[^>]+>", " ", _mail_text(m))
                low = blob.lower()
                if any(k in low for k in ("openai", "chatgpt", "verification code", "verify", "code", "login")):
                    for pat in (r'code to continue:?\s*(\d{6})',
                                r'verification code:?\s*(\d{6})',
                                r'\b(\d{6})\b'):
                        mm = re.search(pat, blob, re.I)
                        if mm:
                            print(f"\n      [+] OTP found: {mm.group(1)}", flush=True)
                            return mm.group(1)
                    seen.add(mid)
        except Exception as e:  # noqa: BLE001
            print(f"      [mail] inbox error: {e}", flush=True)
        print(".", end="", flush=True)
        time.sleep(delay)
    print()
    return None


def wait_for_link(jwt: str, substr: str, max_wait: int = 150, delay: int = 5) -> str | None:
    """Poll our relay for a verification link containing `substr`."""
    start = time.time()
    seen = set()
    while time.time() - start < max_wait:
        try:
            for m in poll_inbox(jwt):
                mid = m.get("id") or m.get("message_id")
                if mid in seen:
                    continue
                blob = _mail_text(m).replace("\\/", "/")
                for url in re.findall(r'https?://[^\s"\'<>)]+', blob):
                    if substr in url:
                        seen.add(mid)
                        return url.rstrip(".,;)\"'")
        except Exception:
            pass
        time.sleep(delay)
    return None


# ───────────────────────────────────────────── session capture



def capture_session(browser) -> dict:
    """Capture the ChatGPT session by opening /api/auth/session directly.

    DrissionPage's run_js does not await promises, so an async fetch() IIFE
    returns nothing. Navigating to the JSON endpoint is synchronous and
    reliable. We do this in a fresh temporary tab so we don't disturb the flow.
    """
    tab = None
    try:
        tab = browser.new_tab("https://chatgpt.com/api/auth/session")
        time.sleep(4)
        raw = tab.run_js("return document.body ? document.body.innerText : ''")
        sess = json.loads(raw) if isinstance(raw, str) and raw.strip().startswith("{") else {}
        # refresh token from cookies
        try:
            for c in browser.cookies(all_domains=True) or []:
                if c.get("name") in ("__Secure-next-auth.session-token",
                                     "next-auth.session-token",
                                     "__Secure-authjs.session-token"):
                    sess["refreshToken"] = c.get("value", "")
        except Exception:
            pass
        return sess
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    finally:
        try:
            if tab:
                tab.close()
        except Exception:
            pass


def save_session(sess: dict, email: str) -> None:
    if not sess or not sess.get("accessToken"):
        print(f"[session] no accessToken ({sess.get('error', 'none')}) — skip", flush=True)
        return
    sess["email"] = sess.get("userEmail") or email
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), SESSION_OUT)
    try:
        data = json.loads(open(path, encoding="utf-8").read()) if os.path.exists(path) else []
        if not isinstance(data, list):
            data = []
    except Exception:
        data = []
    data.append(sess)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"[session] saved -> {path} (plan={sess.get('planType')})", flush=True)


# ───────────────────────────────────────────── helpers

def gen_password() -> str:
    chars = string.ascii_letters + string.digits
    return f"TeacherK12!{''.join(random.choices(chars, k=10))}#2026"


def turnstile_ext() -> str | None:
    for c in (os.environ.get("TURNSTILE_PATCH", ""),
              os.path.expanduser("~/petani-proxy/core/turnstilePatch"),
              os.path.expanduser("~/grok-register/turnstilePatch")):
        if c and os.path.isdir(c):
            return c
    return None


def _open_browser():
    co = ChromiumOptions()
    co.auto_port()
    ext = turnstile_ext()
    if ext:
        co.add_extension(ext)
    return Chromium(co)


# ───────────────────────────────────────────── main flow

def run_flow() -> dict:
    print("=" * 60, flush=True)
    print("  AUTO CHATGPT K-12 (KancaHub relay edition)", flush=True)
    print("=" * 60, flush=True)

    if not SUPABASE_URL or not MAIL_KEY:
        print("[!] mail relay not configured (SUPABASE_URL / TMK_KEY)", flush=True)
        return {"success": False, "error": "mail_relay_unconfigured"}

    print("[1/6] Creating mailbox on our relay…", flush=True)
    mail = create_mailbox()
    email, jwt = mail["email"], mail["jwt"]
    print(f"      [+] {email}", flush=True)

    password = gen_password()
    print(f"[2/6] Password: {password}", flush=True)

    print("[3/6] Launching Chromium (stealth)…", flush=True)
    browser = _open_browser()
    tab = browser.latest_tab
    result: dict = {"success": False, "email": email, "password": password}

    try:
        tab.get("https://chatgpt.com/auth/login/?next=%2Fk12-verification")
        time.sleep(4)

        email_input = None
        for _ in range(15):
            email_input = (tab.ele('tag:input@type=email', timeout=1)
                           or tab.ele('tag:input@name=email', timeout=1)
                           or tab.ele('tag:input', timeout=1))
            if email_input:
                break
            time.sleep(1)

        if not email_input:
            result["error"] = "email_input_not_found"
            return result
        email_input.clear()
        email_input.input(email)
        time.sleep(1)
        btn = tab.ele('tag:button@type=submit', timeout=2) or tab.ele('text:Continue', timeout=2)
        if btn:
            btn.click()
            time.sleep(3)

        # password step (create account) may appear
        pwd = tab.ele('tag:input@type=password', timeout=3)
        if pwd:
            pwd.input(password)
            time.sleep(1)
            b2 = tab.ele('tag:button@type=submit', timeout=2) or tab.ele('text:Continue', timeout=2)
            if b2:
                b2.click()
                time.sleep(3)

        # OTP
        print("[4/6] Waiting for OpenAI OTP from our relay…", flush=True)
        otp = wait_for_otp(jwt, max_wait=180)
        if otp:
            filled = False
            for inp in tab.eles('tag:input'):
                t = inp.attr("type") or ""
                if t in ("text", "number", "tel") or inp.attr("autocomplete") == "one-time-code" \
                        or inp.attr("name") in ("code", "otp"):
                    inp.clear()
                    inp.input(otp)
                    time.sleep(1)
                    filled = True
                    break
            if not filled:
                # fallback: first visible text input
                for inp in tab.eles('tag:input'):
                    inp.input(otp); break
            sb = tab.ele('tag:button@type=submit', timeout=2) or tab.ele('text:Continue', timeout=2)
            if sb:
                sb.click()
                time.sleep(4)
            print("      [+] OTP submitted", flush=True)
        else:
            print("      [!] OTP not found in time", flush=True)

        # capture session (retry a few times — the session endpoint can lag
        # right after signup/OTP).
        sess = None
        for _ in range(6):
            time.sleep(3)
            sess = capture_session(browser)
            if sess.get("accessToken"):
                print("      [+] session captured", flush=True)
                save_session(sess, email)
                break
        if not (sess and sess.get("accessToken")):
            print(f"      [!] session not captured yet ({sess.get('error') if sess else 'none'})", flush=True)

        # about-you
        print("[5/6] Handling profile / SheerID handoff…", flush=True)
        for _ in range(15):
            if "about-you" in tab.url or tab.ele('tag:input@name=name', timeout=1):
                ni = tab.ele('tag:input@name=name', timeout=2)
                if ni:
                    fn = ["James", "Robert", "John", "Michael", "David", "Richard"]
                    ln = ["Miller", "Smith", "Johnson", "Williams", "Brown"]
                    ni.input(f"{random.choice(fn)} {random.choice(ln)}")
                ai = tab.ele('tag:input@name=age', timeout=2)
                if ai:
                    ai.input(str(random.randint(30, 48)))
                time.sleep(1)
                ab = tab.ele('tag:button@type=submit', timeout=2) or tab.ele('text:Continue', timeout=2)
                if ab:
                    ab.click(); time.sleep(3)
                break
            if "k12-verification" in tab.url:
                break
            time.sleep(1)

        # --- actively drive the K-12 verification page ---
        sheerid = None
        for round_ in range(20):
            time.sleep(2)
            # 1) any tab already on sheerid?
            for t in browser.get_tabs():
                if "sheerid.com/verify" in (t.url or ""):
                    sheerid = t.url
                    break
            if sheerid:
                break
            # 2) any <a> pointing at sheerid?
            for a in tab.eles('tag:a'):
                h = a.attr('href') or ""
                if "sheerid.com/verify" in h:
                    sheerid = h
                    break
            if sheerid:
                break
            # 3) click the "Verify status" / "Get started" / "Verify" button
            clicked = False
            for label in ("Verify status", "Get started", "Verify", "Continue", "Start verification"):
                btn = tab.ele(f'text:{label}', timeout=1)
                if btn:
                    try:
                        btn.click()
                        clicked = True
                        time.sleep(4)
                    except Exception:
                        pass
                    break
            # sometimes it opens a new tab; re-scan
            if clicked:
                for t in browser.get_tabs():
                    if "sheerid.com/verify" in (t.url or ""):
                        sheerid = t.url
                        break
                if sheerid:
                    break
            time.sleep(1)

        if sheerid and K12Verifier:
            print("[6/6] Running K12Verifier…", flush=True)
            res = K12Verifier(sheerid, use_temp_email=True, manual_email=email).verify()
            result["verify"] = res
            result["success"] = bool(res.get("success"))
            time.sleep(3)
            sess2 = capture_session(browser)
            if sess2.get("accessToken"):
                save_session(sess2, email)
        else:
            result["error"] = f"no_sheerid_url (url={tab.url[:80]})"

        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "created_k12_accounts.txt")
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"{email}----{password}----{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        print(f"\n[+] creds -> {out}", flush=True)
        return result

    finally:
        time.sleep(2)
        try:
            browser.quit()
        except Exception:
            pass


if __name__ == "__main__":
    r = run_flow()
    print(json.dumps(r, indent=2))
