#!/usr/bin/env python3
"""OpenCode Console device-flow auth: automated browser extraction -> device approve -> tokens.

Reverse-engineered from opencode v2.0.32 and the OpenCode Console bundle:
  client_id      "opencode-cli" (hardcoded in CLI binary)
  code endpoint  POST {console}/auth/device/code   {client_id, supports_org_scope}
  details        GET  {console}/auth/device/details?user_code=...  (session cookie required)
  approve        POST {console}/auth/device/approve {user_code, org_id} (session cookie required)
  poll token     POST {console}/auth/device/token   {grant_type: device_code, device_code, client_id}

Console = https://opencode.ai/console.
Credentials are automatically stored in ~/.local/share/opencode/opencode.db and mirrored
to ~/.local/share/opencode/auth.json.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

CONSOLE = "https://opencode.ai/console"
CLIENT_ID = "opencode-cli"
AUTH_JSON = Path.home() / ".local/share/opencode/auth.json"
OPENCODE_DB = Path.home() / ".local/share/opencode/opencode.db"
SESSION_CACHE_USER = Path.home() / ".config/auto-freecf/opencode_user_session.txt"
SESSION_CACHE_PERSONAL = Path.home() / ".config/auto-freecf/opencode_personal_session.txt"
UA = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OpenCode-User/1.0; +https://opencode.ai"


def _req(url: str, payload: dict | None = None, *, cookie: str = "", method: str = "") -> tuple[int, dict, str]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
    req.add_header("content-type", "application/json")
    req.add_header("user-agent", UA)
    req.add_header("origin", "https://opencode.ai")
    req.add_header("referer", "https://opencode.ai/console/")
    if cookie:
        req.add_header("cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode()
            return r.status, dict(r.headers), body
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()
    except Exception as e:
        return 0, {}, str(e)


def _json(body: str) -> dict:
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body}


def _test_session(cookie: str) -> dict | None:
    if not cookie:
        return None
    st, _, body = _req(f"{CONSOLE}/auth/session", cookie=cookie, method="GET")
    if st == 200:
        d = _json(body)
        if "user" in d:
            return d
    return None


def _get_org_id(cookie: str) -> str | None:
    st, _, body = _req(f"{CONSOLE}/api/orgs", cookie=cookie, method="GET")
    if st == 200:
        d = _json(body)
        if isinstance(d, list) and len(d) > 0 and "id" in d[0]:
            return d[0]["id"]
    return None


def extract_browser_session(personal: bool = False) -> tuple[str, dict | None]:
    """Find and validate active OpenCode Console session cookie.

    personal=False: User / disposable mode (never touches operator's personal Brave cookies).
    personal=True : Operator mode (extracts session from Brave browser KWallet).
    """
    if not personal:
        env_cookie = os.environ.get("OPENCODE_USER_SESSION", "").strip() or os.environ.get("OPENCODE_SESSION", "").strip()
        if env_cookie:
            sess = _test_session(env_cookie)
            if sess:
                return env_cookie, sess
        if SESSION_CACHE_USER.is_file():
            cached = SESSION_CACHE_USER.read_text().strip()
            if cached:
                sess = _test_session(cached)
                if sess:
                    return cached, sess
        return "", None

    # Operator Personal Mode: read cache or decrypt from Brave Browser
    if SESSION_CACHE_PERSONAL.is_file():
        cached = SESSION_CACHE_PERSONAL.read_text().strip()
        if cached:
            sess = _test_session(cached)
            if sess:
                return cached, sess
    brave_db = Path.home() / ".config/BraveSoftware/Brave-Browser/Default/Cookies"
    if brave_db.is_file():
        script = """
import sqlite3, hashlib, dbus
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

brave_db = Path.home() / ".config/BraveSoftware/Brave-Browser/Default/Cookies"
if not brave_db.is_file():
    exit(0)
con = sqlite3.connect(f"file:{brave_db}?immutable=1", uri=True)
rows = con.execute("SELECT name, encrypted_value FROM cookies WHERE host_key LIKE '%opencode%'").fetchall()
if not rows:
    exit(0)

bus = dbus.SessionBus()
kwallet = bus.get_object("org.kde.kwalletd6", "/modules/kwalletd6")
niface = dbus.Interface(kwallet, "org.kde.KWallet")
handle = niface.open("kdewallet", 0, "AutoFreeCF")
raw_pass = niface.readPassword(handle, "Brave Keys", "Brave Safe Storage", "AutoFreeCF")
key16 = hashlib.pbkdf2_hmac("sha1", raw_pass.encode("utf-8"), b"saltysalt", 1, 16)

def decrypt_v11(enc):
    if enc.startswith(b"v11") and len(enc) >= 35:
        cipher = Cipher(algorithms.AES(key16), modes.CBC(enc[19:35]), backend=default_backend())
        dec = cipher.decryptor().update(enc[35:]) + cipher.decryptor().finalize()
        pad = dec[-1]
        return (dec[:-pad] if pad < 16 else dec).decode("utf-8", errors="ignore")
    return ""

cookies = {name: decrypt_v11(enc) for name, enc in rows if decrypt_v11(enc)}
if "auth" in cookies or "__Host-console_session" in cookies:
    print("; ".join(f"{k}={v}" for k, v in cookies.items()))
"""
        for py_bin in ["/usr/bin/python3", sys.executable]:
            try:
                out = subprocess.check_output([py_bin, "-c", script], text=True, stderr=subprocess.DEVNULL).strip()
                if out:
                    sess = _test_session(out)
                    if sess:
                        SESSION_CACHE_PERSONAL.parent.mkdir(parents=True, exist_ok=True)
                        SESSION_CACHE_PERSONAL.write_text(out)
                        return out, sess
            except Exception:
                pass

    return "", None


def start_device(*, quiet: bool = False) -> dict:
    st, _, body = _req(f"{CONSOLE}/auth/device/code",
                       {"client_id": CLIENT_ID, "supports_org_scope": True})
    if st != 200:
        sys.exit(f"device/code HTTP {st}: {body[:200]}")
    d = _json(body)
    url = d.get("verification_uri_complete", "/console/device")
    if not quiet:
        print(f"user_code : {d.get('user_code')}")
        print(f"verify    : https://opencode.ai{url if url.startswith('/') else '/' + url}")
        print(f"expires   : {d.get('expires_in', 600)}s, poll every {d.get('interval', 5)}s")
    return d


def poll_token(device_code: str, interval: int, expires_in: int) -> dict | None:
    deadline = time.time() + expires_in
    while time.time() < deadline:
        st, _, body = _req(f"{CONSOLE}/auth/device/token", {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
            "client_id": CLIENT_ID,
        })
        d = _json(body)
        if st == 200 and "access_token" in d:
            return d
        err = d.get("error", body[:120])
        if err == "authorization_pending":
            time.sleep(interval)
        elif err == "slow_down":
            interval += 5
            time.sleep(interval)
        else:
            print(f"device/token failed: {err}")
            return None
    return None


def approve(user_code: str, cookie: str, org_id: str | None = None) -> bool:
    if not org_id:
        org_id = _get_org_id(cookie)
    payload = {"user_code": user_code}
    if org_id:
        payload["org_id"] = org_id
    st, _, body = _req(f"{CONSOLE}/auth/device/approve", payload, cookie=cookie)
    d = _json(body)
    return st == 200 and d.get("ok") is True


def save_auth(token: dict) -> None:
    AUTH_JSON.parent.mkdir(parents=True, exist_ok=True)
    auth = json.loads(AUTH_JSON.read_text()) if AUTH_JSON.exists() else {}
    auth["opencode"] = {
        "type": "oauth",
        "access": token.get("access_token", ""),
        "refresh": token.get("refresh_token", ""),
        "expires": int(time.time()) + int(token.get("expires_in", 2592000)),
    }
    AUTH_JSON.write_text(json.dumps(auth, indent=2))


def save_db_credential(token: dict, email: str, org_id: str | None = None, label: str = "User") -> None:
    if not OPENCODE_DB.is_file():
        return
    try:
        con = sqlite3.connect(OPENCODE_DB)
        now_ms = int(time.time() * 1000)
        expires_ms = now_ms + int(token.get("expires_in", 2592000)) * 1000
        val = {
            "type": "oauth",
            "methodID": "device",
            "refresh": token.get("refresh_token", ""),
            "access": token.get("access_token", ""),
            "expires": expires_ms,
            "metadata": {
                "server": CONSOLE,
                "accountID": token.get("account_id") or "",
                "email": email or "",
                "orgID": org_id or "",
                "orgName": label,
            },
        }
        row = con.execute("SELECT id FROM credential WHERE integration_id='opencode'").fetchone()
        if row:
            cred_id = row[0]
            con.execute("UPDATE credential SET label=?, value=?, active=1, time_updated=? WHERE id=?",
                        (label, json.dumps(val), now_ms, cred_id))
        else:
            cred_id = f"cred_{uuid.uuid4().hex[:14]}"
            con.execute(
                "INSERT INTO credential (id, integration_id, label, value, active, time_created, time_updated) "
                "VALUES (?, 'opencode', ?, ?, 1, ?, ?)",
                (cred_id, label, json.dumps(val), now_ms, now_ms),
            )
        con.commit()
        con.close()
    except Exception as e:
        print(f"  warning: opencode.db update failed: {e}")


def auto_login(personal: bool = False, interactive: bool = True) -> int:
    mode_name = "Personal (Brave)" if personal else "User (Disposable)"
    print(f"[*] OpenCode Login Mode: {mode_name}")
    cookie, sess = extract_browser_session(personal=personal)
    if cookie and sess:
        email = sess.get("user", {}).get("email", "unknown")
        print(f"✓ Found active session: {email}")
        if org_id:
            print(f"  workspace/org: {org_id}")

        opencode_bin = shutil.which("opencode") or os.environ.get("OPENCODE_BIN") or str(Path.home() / ".opencode/bin/opencode")
        if Path(opencode_bin).is_file():
            print(f"  running: {opencode_bin} auth login opencode --method device...")
            proc = subprocess.Popen(
                [opencode_bin, "auth", "login", "opencode", "--method", "device"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            user_code = None
            start = time.time()
            while time.time() - start < 15:
                line = proc.stdout.readline()
                if not line:
                    break
                m = re.search(r'([A-Z0-9]{4}-[A-Z0-9]{4})', line)
                if m:
                    user_code = m.group(1)
                    break

            if user_code:
                print(f"  intercepted device code: {user_code}")
                ok = approve(user_code, cookie, org_id=org_id)
                if ok:
                    print(f"  ✓ auto-approved code {user_code} via OpenCode Console API")
                    stdout, _ = proc.communicate(timeout=20)
                    if proc.returncode == 0:
                        print(f"✓ OpenCode CLI connected successfully! ({email}) [{mode_name}]")
                        save_db_credential({"access_token": "cli_active"}, email, org_id, label=mode_name)
                        return 0
                    print(f"CLI output:\n{stdout}")
            proc.kill()

        # Fallback to direct API handshake
        print("  performing direct API device handshake...")
        d = start_device(quiet=True)
        user_code = d["user_code"]
        device_code = d["device_code"]
        ok = approve(user_code, cookie, org_id=org_id)
        if not ok:
            print(f"✗ Failed to approve device code {user_code}")
            return 1
        print(f"  ✓ approved device code {user_code}")
        tok = poll_token(device_code, d["interval"], d["expires_in"])
        if not tok or "access_token" not in tok:
            print(f"✗ Failed to acquire token for {user_code}")
            return 1
        save_auth(tok)
        save_db_credential(tok, email, org_id, label=mode_name)
        print(f"✓ OpenCode device authorization completed! ({email}) [{mode_name}]")
        return 0

    if personal:
        print("✗ No active personal OpenCode session found in Brave browser.")
        return 1

    print("No active disposable session cached. Starting fresh device authorization...")
    user_code = d["user_code"]
    device_code = d["device_code"]
    url = f"https://opencode.ai{d['verification_uri_complete'] if d['verification_uri_complete'].startswith('/') else '/' + d['verification_uri_complete']}"
    print(f"\nPlease open the link below to authorize this device:")
    print(f"  {url}\n")
    if interactive:
        try:
            import webbrowser
            webbrowser.open(url)
        except Exception:
            pass

    print("Waiting for authorization (press Ctrl+C to cancel)...")
    tok = poll_token(device_code, d["interval"], d["expires_in"])
    if not tok:
        print("✗ Device flow expired without approval.")
        return 1
    save_auth(tok)
    print("✓ OpenCode authorization saved successfully!")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sp_d = sub.add_parser("device", help="start automated device flow (personal or user mode)")
    sp_d.add_argument("--personal", action="store_true", help="use operator personal Brave browser session")
    sp_a = sub.add_parser("auto", help="alias for device flow")
    sp_a.add_argument("--personal", action="store_true", help="use operator personal Brave browser session")
    sp = sub.add_parser("approve")
    sp.add_argument("user_code", help="user code to approve")
    sp.add_argument("--personal", action="store_true", help="approve using personal session")
    sp.add_argument("--org-id", default=None, help="organization/workspace ID")
    sp.add_argument("--cookie", default=None, help="explicit session cookie")
    a = p.parse_args()

    if a.cmd in ("device", "auto"):
        sys.exit(auto_login(personal=getattr(a, "personal", False), interactive=True))
    elif a.cmd == "approve":
        cookie = a.cookie
        if not cookie:
            cookie, _ = extract_browser_session(personal=getattr(a, "personal", False))
            sys.exit("✗ No session cookie provided or found in browser")
        ok = approve(a.user_code, cookie, org_id=a.org_id)
        if ok:
            print(f"✓ User code {a.user_code} approved successfully")
        else:
            sys.exit(f"✗ Failed to approve user code {a.user_code}")


if __name__ == "__main__":
    main()
