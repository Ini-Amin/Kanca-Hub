#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Auto ChatGPT K-12 Teacher Workspace Creator & Verifier  (KancaHub edition)

Based on Farm-Acc-ChatGPT-K-12-Teachers/PyRuntime_64/auto_k12_flow.py, with two
production fixes:

  1. Captures the ChatGPT OAuth session after login (accessToken/idToken/account)
     and saves it to k12_sessions.json, so the account can be injected into
     9Router's `codex` provider via scripts/chatgpt_9router.py.
  2. Cross-platform turnstilePatch path (was hardcoded to a Windows D-drive path).

Everything else (temp.tf email, OTP polling, SheerID handoff, K12Verifier) is
unchanged. Run from the K-12 tool directory so `from script import K12Verifier`
resolves:

    cd ~/petani-proxy/Farm-Acc-ChatGPT-K-12-Teachers/PyRuntime_64
    KANCAHUB=1 python /path/to/auto_k12_flow_kancahub.py
"""

import os
import sys
import time
import json
import re
import random
import string
import urllib.request
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

TEMP_TF_ACCOUNT_API = "https://temp.tf/api/account?providers=high.edu.pl,outlook.com,hotmail.com,gmail.com&dot=1&plus=1"
TEMP_TF_CHECK_API = "https://temp.tf/api/check"

# Where to append captured sessions (consumed by chatgpt_9router.py inject).
# Default to the K-12 tool dir so `kancahub k12 inject` (which looks under
# K12_DIR) finds it regardless of the current working directory. Override with
# K12_SESSION_OUT.
K12_DIR = Path(os.path.expanduser("~/petani-proxy/Farm-Acc-ChatGPT-K-12-Teachers/PyRuntime_64"))
_default_sessions = (K12_DIR / "k12_sessions.json") if K12_DIR.exists() else (Path(__file__).parent / "k12_sessions.json")
SESSION_OUT = os.environ.get("K12_SESSION_OUT", str(_default_sessions))


# ─────────────────────────────────────────────────── session capture

_SESSION_JS = r"""
(async () => {
  try {
    const r = await fetch('/api/auth/session', { credentials: 'include' });
    const j = await r.json();
    return JSON.stringify({
      accessToken: j.accessToken || '',
      idToken: j.idToken || '',
      expires: j.expires || '',
      userEmail: (j.user && j.user.email) || '',
      accountId: (j.account && j.account.id) || '',
      planType: (j.account && (j.account.planType || j.account.plan_type)) || ''
    });
  } catch (e) { return JSON.stringify({ error: String(e) }); }
})()
"""


def capture_chatgpt_session(tab) -> dict:
    """Extract the ChatGPT session from a DrissionPage tab + its cookies."""
    try:
        raw = tab.run_js(_SESSION_JS)
    except Exception as e:
        return {"error": f"run_js failed: {e}"}
    try:
        sess = json.loads(raw) if isinstance(raw, str) else (raw or {})
    except Exception:
        sess = {"error": f"unparsable: {str(raw)[:120]}"}

    # refresh token from cookies
    try:
        for c in tab.cookies(all_domains=True) or []:
            name = c.get("name", "")
            if name in ("__Secure-next-auth.session-token",
                        "next-auth.session-token",
                        "__Secure-authjs.session-token"):
                sess["refreshToken"] = c.get("value", "")
    except Exception:
        pass
    return sess


def save_session(sess: dict, email: str) -> None:
    """Append the session to SESSION_OUT (list-of-dicts, atomic-ish)."""
    if not sess or not sess.get("accessToken"):
        print("[session] no accessToken captured — skipping save", flush=True)
        return
    sess["email"] = sess.get("userEmail") or email
    path = SESSION_OUT if os.path.isabs(SESSION_OUT) else os.path.join(os.path.dirname(os.path.abspath(__file__)), SESSION_OUT)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        data = json.loads(open(path, encoding="utf-8").read()) if os.path.exists(path) else []
        if not isinstance(data, list):
            data = []
    except Exception:
        data = []
    data.append(sess)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"[session] saved ChatGPT session -> {path}", flush=True)


# ─────────────────────────────────────────────────── original helpers

def get_temp_edu_email():
    print("[1/6] Mengambil email .edu resmi dari temp.tf...", flush=True)
    res = requests.get(TEMP_TF_ACCOUNT_API, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    data = res.json()
    email = data.get("email")
    if not email:
        raise RuntimeError("Gagal mendapatkan email dari temp.tf")
    print(f"      [+] Email diperoleh: {email}", flush=True)
    return email


def poll_temp_tf_otp(email, max_wait=90, delay=3):
    print(f"[4/6] Menunggu kode OTP verifikasi OpenAI di temp.tf (maks {max_wait}s)...", flush=True)
    start = time.time()
    while time.time() - start < max_wait:
        time.sleep(delay)
        try:
            res = requests.post(TEMP_TF_CHECK_API, json={"email": email, "wait": False},
                                headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if res.status_code == 200:
                data = res.json()
                for msg in data.get("data", []):
                    subject = str(msg.get("subject", "")).lower()
                    raw_body = msg.get("body", "") or msg.get("text", "") or msg.get("html", "")
                    clean_body = re.sub(r'<style.*?</style>', '', raw_body, flags=re.DOTALL)
                    clean_body = re.sub(r'<[^>]+>', ' ', clean_body)
                    if any(k in subject or k in clean_body.lower()
                           for k in ["openai", "chatgpt", "verification code", "verify", "code"]):
                        for pat in (r'code to continue:?\s*(\b\d{6}\b)', r'verification code:?\s*(\b\d{6}\b)'):
                            m = re.search(pat, clean_body, re.IGNORECASE)
                            if m:
                                print(f"\n      [+] KODE OTP OPENAI DITEMUKAN: {m.group(1)}", flush=True)
                                return m.group(1)
                        codes = re.findall(r'\b\d{6}\b', clean_body)
                        if codes:
                            print(f"\n      [+] KODE OTP DITEMUKAN: {codes[-1]}", flush=True)
                            return codes[-1]
        except Exception:
            pass
        print(".", end="", flush=True)
    print()
    return None


def generate_strong_password():
    chars = string.ascii_letters + string.digits
    core = "".join(random.choices(chars, k=10))
    return f"TeacherK12!{core}#2026"


def _turnstile_ext() -> str | None:
    """Cross-platform turnstilePatch lookup (was hardcoded to D:\\...)."""
    candidates = [
        os.environ.get("TURNSTILE_PATCH", ""),
        os.path.expanduser("~/petani-proxy/core/turnstilePatch"),
        os.path.expanduser("~/grok-register/turnstilePatch"),
        "/home/amen/petani-proxy/core/turnstilePatch",
    ]
    for c in candidates:
        if c and os.path.isdir(c):
            return c
    return None


def run_flow():
    print("=" * 60, flush=True)
    print("  AUTO CHATGPT K-12 TEACHER ONBOARDING (KancaHub edition)", flush=True)
    print("=" * 60, flush=True)

    email = get_temp_edu_email()
    password = generate_strong_password()
    print(f"[2/6] Password disiapkan: {password}", flush=True)

    print("[3/6] Membuka browser DrissionPage (Stealth Mode)...", flush=True)
    co = ChromiumOptions()
    co.auto_port()
    ext = _turnstile_ext()
    if ext:
        co.add_extension(ext)
        print(f"      [+] turnstilePatch: {ext}", flush=True)

    browser = Chromium(co)
    tab = browser.latest_tab

    try:
        tab.get("https://chatgpt.com/auth/login/?next=%2Fk12-verification")
        time.sleep(3)

        email_input = None
        for _ in range(12):
            email_input = (tab.ele('tag:input@type=email', timeout=1)
                           or tab.ele('tag:input@name=email', timeout=1)
                           or tab.ele('tag:input', timeout=1))
            if email_input:
                break
            time.sleep(1)

        if email_input:
            email_input.clear()
            email_input.input(email)
            time.sleep(1)
            continue_btn = tab.ele('tag:button@type=submit', timeout=2) or tab.ele('text:Continue', timeout=2)
            if continue_btn:
                continue_btn.click()
                time.sleep(3)
        else:
            print("[-] Input email tidak ditemukan di halaman.", flush=True)

        pwd_inp = tab.ele('tag:input@type=password', timeout=2)
        if pwd_inp:
            print("      [+] Memasukkan password akun...", flush=True)
            pwd_inp.input(password)
            time.sleep(1)
            btn = tab.ele('tag:button@type=submit', timeout=2) or tab.ele('text:Continue', timeout=2)
            if btn:
                btn.click()
                time.sleep(3)

        otp = poll_temp_tf_otp(email, max_wait=75)
        if otp:
            for inp in tab.eles('tag:input'):
                if (inp.attr("type") in ["text", "number"]
                        or inp.attr("autocomplete") == "one-time-code"
                        or inp.attr("name") == "code"):
                    inp.input(otp)
                    time.sleep(1)
                    break
            otp_btn = tab.ele('tag:button@type=submit', timeout=2) or tab.ele('text:Continue', timeout=2)
            if otp_btn:
                print("      [+] Menyerahkan kode OTP...", flush=True)
                otp_btn.click()
                time.sleep(3)
        else:
            print("[!] OTP belum terdeteksi otomatis.", flush=True)

        # ---- capture session once we're logged in ----
        time.sleep(4)
        sess = capture_chatgpt_session(tab)
        if sess.get("accessToken"):
            print(f"      [+] ChatGPT session captured (plan={sess.get('planType')})", flush=True)
            save_session(sess, email)
        else:
            print(f"      [!] session capture: {sess.get('error', 'no token yet')}", flush=True)

        print("\n[5/6] Memeriksa tahapan profil pendidik & navigasi ke K-12...", flush=True)
        for _ in range(15):
            curr_url = tab.url
            if "about-you" in curr_url or tab.ele('tag:input@name=name', timeout=1):
                print("      [+] Terdeteksi form 'About you'...", flush=True)
                name_inp = tab.ele('tag:input@name=name', timeout=2)
                if name_inp:
                    fn = ["James", "Robert", "John", "Michael", "David", "Richard", "Thomas", "Charles"]
                    ln = ["Miller", "Smith", "Johnson", "Williams", "Brown", "Davis", "Wilson", "Anderson"]
                    name_inp.input(f"{random.choice(fn)} {random.choice(ln)}")
                age_inp = tab.ele('tag:input@name=age', timeout=2)
                if age_inp:
                    age_inp.input(str(random.randint(30, 48)))
                time.sleep(1)
                about_btn = tab.ele('tag:button@type=submit', timeout=2) or tab.ele('text:Continue', timeout=2)
                if about_btn:
                    about_btn.click()
                    time.sleep(3)
                break
            elif "k12-verification" in curr_url:
                break
            time.sleep(1)

        print("      [+] Memantau tombol verifikasi dan tautan SheerID...", flush=True)
        sheerid_url = None
        for _ in range(25):
            time.sleep(2)
            for t in browser.get_tabs():
                if "sheerid.com/verify" in t.url:
                    sheerid_url = t.url
                    print(f"\n[+] BERHASIL MENDETEKSI URL SHEERID: {sheerid_url}", flush=True)
                    break
            if sheerid_url:
                break
            verify_btn = tab.ele('text:Verify status', timeout=1) or tab.ele('text:Verify', timeout=1)
            if verify_btn:
                print("      [+] Mengklik tombol 'Verify status'...", flush=True)
                verify_btn.click()
                time.sleep(3)
            for a in tab.eles('tag:a'):
                href = a.attr('href') or ""
                if "sheerid.com/verify" in href:
                    sheerid_url = href
                    print(f"\n[+] BERHASIL MENDETEKSI LINK SHEERID: {sheerid_url}", flush=True)
                    break
            if sheerid_url:
                break

        if sheerid_url and K12Verifier:
            print("\n[6/6] Menjalankan modul K12Verifier...", flush=True)
            verifier = K12Verifier(sheerid_url, use_temp_email=True, manual_email=email)
            result = verifier.verify()
            print("\nHASIL VERIFIKASI AKHIR:", flush=True)
            print(json.dumps(result, indent=2), flush=True)
            # re-capture session after verification (plan may upgrade to teacher)
            time.sleep(3)
            sess2 = capture_chatgpt_session(tab)
            if sess2.get("accessToken"):
                save_session(sess2, email)
        else:
            print(f"\n[Status] Halaman browser saat ini: {tab.url}", flush=True)

        out_file = os.path.join(os.path.dirname(__file__), "created_k12_accounts.txt")
        with open(out_file, "a", encoding="utf-8") as f:
            f.write(f"{email}----{password}----{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        print(f"\n[+] Kredensial tersimpan: {out_file}", flush=True)
        print(f"    Email   : {email}", flush=True)
        print(f"    Password: {password}", flush=True)

    finally:
        time.sleep(3)
        try:
            browser.quit()
        except Exception:
            pass


if __name__ == "__main__":
    run_flow()
