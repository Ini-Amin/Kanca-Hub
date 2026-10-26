#!/usr/bin/env python3
"""AutoClaw 2.0.3 (Zhipu zwork) API client & reverse-engineered protocol helper.
All endpoints verified live against https://autoglm-api.autoglm.ai.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = "https://autoglm-api.autoglm.ai"
APP_ID = "100003"
APP_KEY = "38d2391985e2369a5fb8227d8e6cd5e5"
DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "AutoClaw/2.0.3 Chrome/120.0.0.0 Electron/28.0.0 Safari/537.36"
)

ACCOUNTS_PATH = Path.home() / ".config" / "auto-freecf" / "autoclaw_accounts.jsonl"
INVITE_CODE_PATH = Path.home() / ".config" / "auto-freecf" / "autoclaw_invite_code.txt"


def get_default_invite_code(path: Path | None = None) -> str:
    """Get the cached default referral invite code if present."""
    p = path or INVITE_CODE_PATH
    if p.exists():
        try:
            return p.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return ""


def set_default_invite_code(code: str, path: Path | None = None) -> None:
    """Cache the default referral invite code."""
    p = path or INVITE_CODE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(code.strip(), encoding="utf-8")



def make_headers(token: str | None = None, lang: str = "en-US") -> dict[str, str]:
    """Build MD5 signed headers required by AutoClaw API gateway."""
    ts = str(int(time.time()))
    sign = hashlib.md5(f"{APP_ID}&{ts}&{APP_KEY}".encode()).hexdigest()
    h = {
        "Content-Type": "application/json",
        "User-Agent": DEFAULT_UA,
        "X-Version": "2.0.3",
        "X-Tm": "linux",
        "X-Product": "autoclaw",
        "X-Channel": "official",
        "X-Lang": lang,
        "X-Auth-Appid": APP_ID,
        "X-Auth-TimeStamp": ts,
        "X-Auth-Sign": sign,
    }
    if token:
        h["Authorization"] = token if token.startswith("Bearer ") else f"Bearer {token}"
    return h


def save_account(record: dict, path: Path | None = None) -> Path:
    """Save an AutoClaw account record to JSONL."""
    p = path or ACCOUNTS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    return p


def load_accounts(path: Path | None = None) -> list[dict]:
    """Load all saved AutoClaw accounts."""
    p = path or ACCOUNTS_PATH
    if not p.exists():
        return []
    records = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except Exception:
            continue
    return records


def latest_account(path: Path | None = None) -> dict | None:
    """Return the most recently saved AutoClaw account."""
    accounts = load_accounts(path)
    return accounts[-1] if accounts else None


def latest_token(path: Path | None = None) -> str | None:
    """Return the latest account access_token, if any."""
    acc = latest_account(path)
    return acc.get("access_token") if acc else None


APP_USER_DATA = Path.home() / ".config" / "AutoClaw-oversea-official"


def load_app_device_id() -> str | None:
    """Device id from the app's userData, so API identity matches the desktop app."""
    try:
        return json.loads((APP_USER_DATA / "device" / "device-id.json").read_text())["deviceId"]
    except Exception:
        return None


def load_app_credentials() -> dict | None:
    """Fresh credentials straight from the app, best-effort.

    1. GET http://127.0.0.1:18432/get_token while the app runs (auto-refreshed).
    2. Decrypt accounts/<key>/account-credentials.enc (OSCrypt basic_text v10:
       AES-128-CBC, key PBKDF2("peanuts","saltysalt",1,16), iv 16x0x20) once the
       vault persists on disk.
    """
    try:
        req = urllib.request.Request("http://127.0.0.1:18432/get_token")
        with urllib.request.urlopen(req, timeout=3) as resp:
            tok = resp.read().decode("utf-8").strip()
            if tok.startswith("Bearer "):
                tok = tok[len("Bearer "):]
            if tok:
                return {"access_token": tok, "device_id": load_app_device_id(), "source": "get_token"}
    except Exception:
        pass

    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        return None
    key = hashlib.pbkdf2_hmac("sha1", b"peanuts", b"saltysalt", 1, 16)
    iv = b" " * 16
    for enc in sorted(APP_USER_DATA.glob("accounts/*/account-credentials.enc")):
        try:
            blob = enc.read_bytes()
            if not blob.startswith(b"v10"):
                continue
            dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
            padded = dec.update(blob[3:]) + dec.finalize()
            pad = padded[-1] if padded else 0
            plain = padded[:-pad] if 1 <= pad <= 16 else padded
            stored = json.loads(plain.decode("utf-8"))
            if stored.get("accessToken"):
                return {"access_token": stored["accessToken"],
                        "refresh_token": stored.get("refreshToken"),
                        "device_id": load_app_device_id(), "source": "vault"}
        except Exception:
            continue
    return None


class AutoClawClient:
    def __init__(self, device_id: str | None = None):
        self.device_id = device_id or load_app_device_id() or str(uuid.uuid4())

    def _req(self, path: str, method: str = "POST", body: dict | None = None, token: str | None = None) -> dict:
        url = f"{BASE}{path}"
        data = json.dumps(body).encode() if body is not None else None
        headers = make_headers(token=token)
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8"))
            except Exception:
                raise e

    # ── Referral & Fission ──
    def activity_status(self) -> dict:
        """Check ongoing fission/referral campaign info."""
        return self._req("/agent-assetmgr/api/v1/fission-v2/online-activity-info?business=autoclaw", method="GET")

    def bind_invite_code(self, invite_code: str, token: str | None = None) -> dict:
        """Bind inviter referral code for the current account."""
        tok = token or latest_token()
        if not tok:
            raise ValueError("No access token provided and no saved accounts found.")
        body = {
            "activity_id": "autoclaw_fission",
            "business": "autoclaw",
            "invite_code": invite_code,
            "source_id": "autoclaw",
            "device_id": self.device_id,
        }
        return self._req("/agent-assetmgr/api/v1/fission-v2/bind", body=body, token=tok)

    def get_wallets(self, token: str | None = None) -> dict:
        """Query balances across wallet scopes (v2, GET with query string)."""
        tok = token or latest_token()
        if not tok:
            raise ValueError("No access token provided and no saved accounts found.")
        return self._req("/agent-assetmgr/api/v2/wallets?biz_app_id=autoclaw", method="GET", token=tok)

    def get_wallet_instances(self, token: str | None = None) -> dict:
        """Per-scope balances incl. `campaign` (40M bonus) — v1 fallback path."""
        tok = token or latest_token()
        if not tok:
            raise ValueError("No access token provided and no saved accounts found.")
        return self._req("/agent-assetmgr/api/v1/wallet-instances?wallet_type=all&wallet_scope=all", method="GET", token=tok)

    def get_ledgers(self, token: str | None = None, page_size: int = 20) -> dict:
        """Credit mutation history."""
        tok = token or latest_token()
        if not tok:
            raise ValueError("No access token provided and no saved accounts found.")
        return self._req(f"/agent-assetmgr/api/v1/ledgers_std?asset_type=point&wallet_type=all&page_size={page_size}", method="GET", token=tok)

    def redeem_code(self, code: str, token: str | None = None) -> dict:
        """Redeem a promo/voucher code."""
        tok = token or latest_token()
        if not tok:
            raise ValueError("No access token provided and no saved accounts found.")
        return self._req("/agent-assetmgr/api/v1/autoglm-codes/redeem", body={"biz_app_id": "autoclaw", "code": code.strip()}, token=tok)

    def claim_daily_credits(self, token: str | None = None) -> dict:
        """Trigger the login/daily/event (40M) credit grant — the app's own claim path."""
        tok = token or latest_token()
        if not tok:
            raise ValueError("No access token provided and no saved accounts found.")
        return self._req("/userapi/v1/user-info", body={"source_id": "autoclaw", "device_id": self.device_id}, token=tok)

    def refresh_token(self, refresh_tok: str) -> dict:
        """Exchange a refresh token for fresh credentials; saves the new pair."""
        body = {"refresh_token": refresh_tok, "source_id": "autoclaw", "device_id": self.device_id}
        res = self._req("/userapi/v1/refresh", body=body)
        if res.get("code") == 400002:
            res = self._req("/userapi/v1/agent-refresh", body=body)
        d = res.get("data") or {}
        if res.get("code") == 0 and d.get("access_token"):
            rec = {
                "user_id": d.get("user_id"),
                "auth_type": "refresh",
                "access_token": d.get("access_token"),
                "refresh_token": d.get("refresh_token") or refresh_tok,
                "device_id": self.device_id,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            save_account(rec)
        return res

    def chat(self, model: str, messages: list, token: str | None = None, session_id: str | None = None, **kw) -> dict:
        """Call a built-in AutoClaw model via the managed model gateway proxy."""
        tok = token or latest_token()
        if not tok:
            raise ValueError("No access token provided and no saved accounts found.")
        ts = str(int(time.time()))
        sign = hashlib.md5(f"{APP_ID}&{ts}&{APP_KEY}".encode()).hexdigest()
        rid = str(uuid.uuid4())
        h = {
            "Content-Type": "application/json",
            "User-Agent": DEFAULT_UA,
            "X-Version": "2.0.3",
            "X-Tm": "linux",
            "X-Product": "autoclaw",
            "X-Channel": "official",
            "X-Lang": "en-US",
            "X-Auth-Appid": APP_ID,
            "X-Auth-TimeStamp": ts,
            "X-Auth-Sign": sign,
            "X-Authorization": f"Bearer {tok}",
            "X-Trace-Id": rid,
            "X-Request-Id": rid,
            "X-Request-Model": model,
            "X-Client-Type": "pc",
            "x_trace_id": "autoclaw-desktop",
        }
        if session_id:
            h["X-Session-Id"] = session_id
        body = {"model": model, "messages": messages, **kw}
        req = urllib.request.Request(f"{BASE}/autoclaw-proxy/proxy/autoclaw/chat/completions",
                                     data=json.dumps(body).encode(), headers=h, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8"))
            except Exception:
                raise e

    # ── z.ai OAuth Flow ──
    def captcha_config(self) -> dict:
        """Fetch Aliyun captcha configuration (region, prefix, scene_id)."""
        return self._req("/userapi/overseasv1/oauth-captcha-config", body={})

    def get_oauth_url(self, captcha_verify_param: str, navigate_uri: str = "http://localhost:18432/auth/callback-zai", provider: str = "zai") -> dict:
        """Get OAuth redirect URL (default: z.ai) after Aliyun captcha verification."""
        path = f"/userapi/overseasv1/{provider}-oauth-url"
        body = {
            "navigate_uri": navigate_uri,
            "ali_captcha_verify_param": captcha_verify_param,
            "source_id": "autoclaw",
            "device_id": self.device_id,
        }
        return self._req(path, body=body)

    def oauth_login(self, code: str, state: str, navigate_uri: str = "http://localhost:18432/auth/callback-zai", provider: str = "zai", bind_code: str | None = None) -> dict:
        """Exchange OAuth authorization code & state for user access tokens."""
        path = f"/userapi/overseasv1/{provider}-oauth-login"
        body = {
            "code": code,
            "state": state,
            "navigate_uri": navigate_uri,
            "source_id": "autoclaw",
            "device_id": self.device_id,
        }
        res = self._req(path, body=body)
        if res.get("code") == 0 and res.get("data"):
            d = res["data"]
            rec = {
                "user_id": d.get("user_id"),
                "user_name": d.get("user_name"),
                "auth_type": f"oauth_{provider}",
                "access_token": d.get("access_token"),
                "refresh_token": d.get("refresh_token"),
                "device_id": self.device_id,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            save_account(rec)
            if bind_code and d.get("access_token"):
                try:
                    bind_res = self.bind_invite_code(bind_code, token=d["access_token"])
                    res["bind_result"] = bind_res
                except Exception as e:
                    res["bind_result"] = {"error": str(e)}
        return res

    def interactive_claim(self, bind_code: str | None = None, port: int = 18432,
                          navigate_uri: str | None = None, provider: str = "zai",
                          timeout: float = 180.0, open_browser: bool = True,
                          browser: str = "auto") -> dict:
        """Full interactive 1-click OAuth claim.

        Two modes, because the Aliyun proof is origin/device bound:

        ``browser="camoufox"`` (default when the venv is present)
            Drives a real Camoufox browser on the REAL origin ``chat.z.ai``:
            inject the captcha there, solve it, call ``zai-oauth-url``, then
            follow the URL in the same session. This is the pattern already
            proven in ``zcode_claim_native.py`` and is the only mode that can
            pass Aliyun's behavioural check.

        ``browser="local"``
            The old localhost page. Kept for debugging the widget only: a proof
            minted on ``localhost`` is rejected by the OAuth API with
            ``630014 Verification failed`` even when the widget itself shows
            ``验证通过``.
        """
        # The appless Camoufox path (mint our own captcha proof on chat.z.ai, then
        # ask for the OAuth URL) is measured-dead: AutoClaw answers 630014 for any
        # proof we mint ourselves. The working path is the app's own auth URL —
        # use claim_from_auth_url() for that. `browser="local"` remains for
        # inspecting the widget.
        if browser == "camoufox":
            return {"code": 1,
                    "msg": "a self-minted captcha proof is rejected (630014); "
                           "run the AutoClaw app and pass its auth URL to "
                           "claim_from_auth_url() instead"}

        from http.server import BaseHTTPRequestHandler, HTTPServer
        import urllib.parse
        import webbrowser

        nav = navigate_uri or f"http://localhost:{port}/auth/callback-{provider}"
        cap_conf = self.captcha_config()
        cap_data = cap_conf.get("data") or {}
        region = cap_data.get("region", "ga")
        prefix = cap_data.get("prefix", "sq51tr")
        scene_id = cap_data.get("scene_id", "18vhnjxl")

        state_box = {
            "running": True,
            "result": None,
            "error": None,
        }

        html_page = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AutoClaw 2.0.3 — z.ai Authorization</title>
  <!-- AliyunCaptchaConfig MUST be set BEFORE the SDK script executes: the SDK
       reads window.AliyunCaptchaConfig at load time and caches it. A plain
       <script src> followed by an inline assignment gives prefix=undefined and
       the widget never opens ("widget would not open" in every headless run). -->
  <script>window.AliyunCaptchaConfig = {{region: "{region}", prefix: "{prefix}"}};</script>
  <script src="https://o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js"></script>
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #0f172a;
      color: #f8fafc;
      display: flex;
      justify-content: center;
      align-items: center;
      min-height: 100vh;
      margin: 0;
    }}
    .card {{
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 16px;
      padding: 32px;
      max-width: 440px;
      width: 100%;
      box-shadow: 0 10px 30px rgba(0,0,0,0.5);
      text-align: center;
    }}
    h2 {{ margin: 0 0 8px; color: #38bdf8; font-size: 1.5rem; }}
    p {{ color: #94a3b8; font-size: 0.95rem; margin: 0 0 20px; line-height: 1.5; }}
    .badge {{
      display: inline-block;
      background: #0369a1;
      color: #e0f2fe;
      padding: 4px 12px;
      border-radius: 9999px;
      font-size: 0.8rem;
      font-weight: 600;
      margin-bottom: 16px;
    }}
    #captcha-element {{
      min-height: 50px;
      display: flex;
      justify-content: center;
      align-items: center;
      margin: 16px 0;
    }}
    #status {{
      font-size: 0.9rem;
      color: #38bdf8;
      min-height: 24px;
      margin-top: 12px;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">+800 TOKENS FISSION BONUS</div>
    <h2>AutoClaw Verification</h2>
    <p>Complete the slider below to authorize your <b>z.ai</b> account with AutoClaw.</p>
    <div id="captcha-element"></div>
    <button id="captcha-trigger" style="display:none;" type="button"></button>
    <div id="status">Loading security verification…</div>
  </div>
  <script>
    function init() {{
      if (typeof window.initAliyunCaptcha !== "function") {{
        setTimeout(init, 200);
        return;
      }}
      try {{
        window.initAliyunCaptcha({{
          SceneId: "{scene_id}",
          prefix: "{prefix}",
          mode: "popup",
          element: "#captcha-element",
          button: "#captcha-trigger",
          captchaVerifyCallback: function(proof) {{
            document.getElementById("status").innerText = "✓ Verified! Requesting z.ai OAuth URL…";
            // The SDK REQUIRES this callback to return a Promise that resolves to
            // {{captchaResult, bizResult}}. Returning undefined makes the SDK treat
            // the business step as failed and silently refresh the puzzle forever
            // (the checkbox ticks, then nothing happens). This mirrors AutoClaw's own
            // buildAliyunCaptchaDocument(), which stores the resolver in
            // window.__zworkCompleteCaptcha and calls it after the OAuth API answers.
            return fetch("/api/solve-callback", {{
              method: "POST",
              headers: {{"Content-Type": "application/json"}},
              body: JSON.stringify({{captcha_verify_param: proof}})
            }})
            .then(res => res.json())
            .then(data => {{
              if (data.oauth_url) {{
                document.getElementById("status").innerText = "Redirecting to z.ai authorization…";
                window.location.href = data.oauth_url;
                return {{captchaResult: true, bizResult: true}};
              }}
              document.getElementById("status").innerText = "✗ Error: " + (data.msg || data.error || "Failed to get OAuth URL");
              return {{captchaResult: true, bizResult: false}};
            }})
            .catch(err => {{
              document.getElementById("status").innerText = "✗ Error: " + err;
              return {{captchaResult: true, bizResult: false}};
            }});
          }},
          onBizResultCallback: function() {{}},
          getInstance: function(inst) {{
            document.getElementById("status").innerText = "Click or slide to verify.";
            setTimeout(function() {{
              const trigger = document.getElementById("captcha-trigger");
              if (trigger) trigger.click();
            }}, 300);
          }}
        }});
      }} catch (e) {{
        document.getElementById("status").innerText = "Init error: " + e;
      }}
    }}
    init();
  </script>
</body>
</html>"""

        client_ref = self

        class InteractiveHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path in ("/", "/captcha"):
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(html_page.encode("utf-8"))
                    return

                if parsed.path == f"/auth/callback-{provider}":
                    qs = urllib.parse.parse_qs(parsed.query)
                    code = qs.get("code", [None])[0]
                    state = qs.get("state", [None])[0]
                    error = qs.get("error", [None])[0]
                    if code and state:
                        login_res = client_ref.oauth_login(code, state, navigate_uri=nav, provider=provider, bind_code=bind_code)
                        state_box["result"] = login_res
                        state_box["running"] = False
                        user_name = (login_res.get("data") or {}).get("user_name") or "User"
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.end_headers()
                        success_html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>AutoClaw — Success</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }}
.card {{ background: #1e293b; border: 1px solid #334155; border-radius: 16px; padding: 32px; max-width: 440px; text-align: center; }}
h2 {{ color: #4ade80; margin-top: 0; }}
p {{ color: #94a3b8; font-size: 0.95rem; line-height: 1.5; }}
</style></head><body><div class="card">
<h2>🎉 AutoClaw Claim Successful!</h2>
<p>Authorized as <b>{user_name}</b>.<br>Referral bonus (+800 tokens) bound successfully.</p>
<p style="color:#64748b;font-size:0.85rem;">You can close this tab and return to the terminal.</p>
</div></body></html>"""
                        self.wfile.write(success_html.encode("utf-8"))
                    else:
                        state_box["error"] = error or "missing_code_or_state"
                        state_box["running"] = False
                        self.send_response(400)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(b"<html><body style='background:#0f172a;color:#f87171;font-family:sans-serif;text-align:center;padding:50px;'><h2>Authorization Failed</h2><p>Missing code or state.</p></body></html>")
                    return

                self.send_response(404)
                self.end_headers()

            def do_POST(self):
                if self.path == "/api/solve-callback":
                    try:
                        length = int(self.headers.get("Content-Length") or 0)
                        body = json.loads(self.rfile.read(length).decode("utf-8"))
                        proof = body.get("captcha_verify_param")
                        if not proof:
                            self.send_response(400)
                            self.send_header("Content-Type", "application/json")
                            self.end_headers()
                            self.wfile.write(json.dumps({"error": "missing captcha_verify_param"}).encode())
                            return

                        url_res = client_ref.get_oauth_url(proof, navigate_uri=nav, provider=provider)
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        if url_res.get("code") == 0 and url_res.get("data"):
                            self.wfile.write(json.dumps({"oauth_url": url_res["data"]["oauth_url"]}).encode())
                        else:
                            self.wfile.write(json.dumps({"error": url_res.get("msg") or "Verification failed", "code": url_res.get("code")}).encode())
                    except Exception as ex:
                        self.send_response(500)
                        self.send_header("Content-Type", "application/json")
                        self.end_headers()
                        self.wfile.write(json.dumps({"error": str(ex)}).encode())
                    return

                self.send_response(404)
                self.end_headers()

            def log_message(self, format, *args):
                pass

        try:
            server = HTTPServer(("127.0.0.1", port), InteractiveHandler)
        except OSError as ex:
            if "Address already in use" in str(ex) or getattr(ex, "errno", None) == 98:
                return {"code": 1, "msg": f"Port {port} is busy — another program is using it. Stop it first, or retry with --port <free-port> (e.g. --port 18433)."}
            raise
        server.timeout = 1.0

        start_url = f"http://localhost:{port}/"
        print(f"Opening your browser at {start_url}")
        print("(If it doesn't open, copy-paste that address into your browser.)")
        if open_browser:
            webbrowser.open(start_url)
        t0 = time.time()
        try:
            while state_box["running"] and (time.time() - t0 < timeout):
                server.handle_request()
        finally:
            server.server_close()

        if state_box["result"]:
            return state_box["result"]
        if state_box["error"]:
            return {"code": 1, "msg": f"Authorization error: {state_box['error']}"}
        return {"code": 1, "msg": f"OAuth claim timed out after {int(timeout)}s"}

    def claim_from_auth_url(self, auth_url: str, email: str, password: str,
                            bind_code: str | None = None, port: int = 18432,
                            navigate_uri: str | None = None, provider: str = "zai",
                            puzzles: int = 25, log=print) -> dict:
        """Finish AutoClaw sign-in using an auth URL produced by the app.

        The app's own Aliyun captcha is the only one AutoClaw accepts: a proof
        we mint ourselves (on chat.z.ai, or on localhost) comes back as
        ``630014 Verification failed`` — measured, both origins. So the app is
        driven normally, it hands us ``chat.z.ai/api/oauth/authorize?...``, and
        this method only does the z.ai half: email sign-in, Aliyun slider,
        consent — all via the shared ``consent_hard`` helpers that
        ``zcode_bridge.py`` already uses.

        ``port`` must match the ``redirect_uri`` the app registered (18432 by
        default); the app's own loopback listener catches the code and performs
        the exchange itself, so no exchange happens here.
        """
        import asyncio
        from camoufox.async_api import AsyncCamoufox
        from consent_hard import accept_consent, signin_zai_email

        nav = navigate_uri or f"http://localhost:{port}/auth/callback-{provider}"

        async def drive() -> dict:
            async with AsyncCamoufox(headless=False, humanize=True, os="windows",
                                     locale="en-US") as br:
                ctx = await br.new_context()
                page = await ctx.new_page()
                await page.goto(auth_url, wait_until="domcontentloaded", timeout=90000)
                await page.wait_for_timeout(5000)

                if not await signin_zai_email(page, email, password,
                                              puzzles=puzzles, log=log):
                    return {"code": 1, "msg": "z.ai sign-in failed"}

                if "/auth/oauth/authorize" in page.url and "code=" not in page.url:
                    if not await accept_consent(page):
                        return {"code": 1, "msg": "consent not completed"}

                for _ in range(30):
                    if "localhost" in page.url and "code=" in page.url:
                        return {"code": 0, "redirected": True, "url": page.url[:160]}
                    await page.wait_for_timeout(1000)
                return {"code": 1, "msg": "no redirect back to the app",
                        "url": page.url[:160]}

        return asyncio.run(drive())

    @staticmethod
    def listen_oauth_callback(port: int = 18432, path: str = "/auth/callback-zai", timeout: float = 120.0) -> dict[str, str]:
        """Listen on local loopback for OAuth redirect callback from browser."""
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import urllib.parse

        captured: dict[str, str] = {}

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != path:
                    self.send_response(404)
                    self.end_headers()
                    return
                qs = urllib.parse.parse_qs(parsed.query)
                code = qs.get("code", [None])[0]
                state = qs.get("state", [None])[0]
                error = qs.get("error", [None])[0]
                if code and state:
                    captured["code"] = code
                    captured["state"] = state
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"<html><body style='font-family:sans-serif;text-align:center;padding:50px;'><h2>AutoClaw z.ai Login Successful!</h2><p>You can close this tab and return to the terminal.</p></body></html>")
                else:
                    captured["error"] = error or "missing_code_or_state"
                    self.send_response(400)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(b"<html><body style='font-family:sans-serif;text-align:center;padding:50px;'><h2>Authorization Failed</h2><p>Missing code or state.</p></body></html>")

            def log_message(self, format, *args):
                pass

        server = HTTPServer(("127.0.0.1", port), CallbackHandler)
        server.timeout = timeout
        server.handle_request()
        server.server_close()
        return captured

if __name__ == "__main__":
    client = AutoClawClient()
    print("Testing AutoClaw activity status...")
    act = client.activity_status()
    print("Activity:", json.dumps(act, indent=2))
    print("\nTesting Captcha config...")
    cap = client.captcha_config()
    print("Captcha:", json.dumps(cap, indent=2))
