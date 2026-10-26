#!/usr/bin/env python3
"""omnirush_bridge.py — OpenAI-compatible Bridge for OmniRush AI Gateway.

Provides free frontier AI model access via OmniRush (https://omnirush.ai).
Features:
  1. RFC 8628 OAuth Device Flow (`login` command)
  2. Auto-refresh tokens via /device/refresh
  3. Translates OpenAI chat completions to OmniRush Responses API (/v1/responses)
  4. Listens on http://127.0.0.1:8795

CLI:
  python3 scripts/omnirush_bridge.py login   # Authorize in browser
  python3 scripts/omnirush_bridge.py serve   # Start bridge server (:8795)
  python3 scripts/omnirush_bridge.py status  # Check tokens & quota
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_PORT = 8795
CONFIG_DIR = Path.home() / ".config" / "auto-freecf"
TOKEN_FILE = CONFIG_DIR / "omnirush_tokens.json"

OMNI_ORIGIN = "https://omnirush.ai/omnirush"
CLIENT_VERSION = "2.7.0"
DEFAULT_USER_AGENT = "omnirush"

HEADERS_AUTH = {
    "User-Agent": f"omnirush/{CLIENT_VERSION} ({platform.system().lower()}; {platform.machine().lower()})",
    "x-omnirush-client": f"cli/{CLIENT_VERSION}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}

HEADERS_API = {
    "User-Agent": DEFAULT_USER_AGENT,
    "x-omnirush-client": f"cli/{CLIENT_VERSION}",
    "Content-Type": "application/json",
    "Accept": "text/event-stream, application/json",
}

logging.basicConfig(
    level=logging.INFO,
    format="[omnirush-bridge] %(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("omnirush-bridge")


def load_tokens() -> dict | None:
    if not TOKEN_FILE.exists():
        return None
    try:
        data = json.loads(TOKEN_FILE.read_text())
        if data.get("access_token"):
            return data
    except Exception:
        pass
    return None


def save_tokens(data: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(data, indent=2))
    log.info("Saved tokens to %s", TOKEN_FILE)


def refresh_tokens(refresh_token: str) -> dict | None:
    url = f"{OMNI_ORIGIN}/device/refresh"
    payload = {"refresh_token": refresh_token}
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=HEADERS_AUTH)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode())
            if data.get("access_token"):
                tokens = {
                    "access_token": data["access_token"],
                    "refresh_token": data.get("refresh_token") or refresh_token,
                    "expires_in": data.get("expires_in"),
                    "updated_at": time.time(),
                }
                save_tokens(tokens)
                return tokens
    except Exception as e:
        log.error("Token refresh failed: %s", e)
    return None


def get_valid_token() -> str:
    tokens = load_tokens()
    if not tokens:
        raise RuntimeError("OmniRush not logged in. Run 'python3 scripts/omnirush_bridge.py login' first.")
    # Check if access token is likely expired (e.g. older than expires_in or 1 hour)
    updated = tokens.get("updated_at", 0)
    expires_in = tokens.get("expires_in") or 3600
    if time.time() - updated > (expires_in - 120) and tokens.get("refresh_token"):
        log.info("Access token near expiry, refreshing...")
        refreshed = refresh_tokens(tokens["refresh_token"])
        if refreshed:
            return refreshed["access_token"]
    return tokens["access_token"]


def do_login():
    print("=" * 60)
    print("  OmniRush Device Login (OAuth RFC 8628)")
    print("=" * 60)

    url_auth = f"{OMNI_ORIGIN}/device/authorize"
    body = {
        "device_name": f"kancahub-{platform.node()[:10]}",
        "platform": platform.system().lower(),
    }
    req = urllib.request.Request(url_auth, data=json.dumps(body).encode(), headers=HEADERS_AUTH)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            auth_data = json.loads(r.read().decode())
    except Exception as e:
        sys.exit(f"Failed to request device authorization: {e}")

    device_code = auth_data["device_code"]
    user_code = auth_data["user_code"]
    verify_url = auth_data.get("verification_uri_complete") or f"{auth_data['verification_uri']}?code={user_code}"
    interval = max(auth_data.get("interval", 3), 2)
    expires_in = auth_data.get("expires_in", 600)

    print(f"\n[1/2] User Code: {user_code}")
    print(f"      Verification URL: {verify_url}\n")
    print("Silakan buka tautan di atas di browser Anda dan klik 'Confirm' / 'Authorize'.")

    # Try opening browser on desktop if available
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        try:
            subprocess.Popen(["xdg-open", verify_url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("      (Browser otomatis dibuka di desktop Anda)")
        except Exception:
            pass

    print(f"\n[2/2] Menunggu otorisasi di browser (polling setiap {interval}s)...")
    url_token = f"{OMNI_ORIGIN}/device/token"
    start_time = time.time()

    while time.time() - start_time < expires_in:
        req_token = urllib.request.Request(url_token, data=json.dumps({"device_code": device_code}).encode(), headers=HEADERS_AUTH)
        try:
            with urllib.request.urlopen(req_token, timeout=15) as r:
                tdata = json.loads(r.read().decode())
                if tdata.get("access_token"):
                    tokens = {
                        "access_token": tdata["access_token"],
                        "refresh_token": tdata.get("refresh_token"),
                        "expires_in": tdata.get("expires_in"),
                        "gateway_url": tdata.get("gateway_url"),
                        "updated_at": time.time(),
                    }
                    save_tokens(tokens)
                    print("\n🎉 LOGIN BERHASIL! Token akses tersimpan.")
                    return
        except urllib.error.HTTPError as e:
            if e.code == 428:
                # authorization_pending, keep waiting
                pass
            elif e.code == 400:
                sys.exit("\n[✗] Kode otorisasi kedaluwarsa.")
            else:
                log.warning("Token check HTTP %s: %s", e.code, e.read().decode())
        except Exception as e:
            log.warning("Polling error: %s", e)

        print(".", end="", flush=True)
        time.sleep(interval)

    sys.exit("\n[✗] Timeout menunggu otorisasi.")


def check_status():
    tokens = load_tokens()
    if not tokens:
        print("[✗] OmniRush: Not logged in.")
        return
    print(f"[✓] OmniRush: Logged in (Token length: {len(tokens['access_token'])})")
    url_me = f"{OMNI_ORIGIN}/device/me"
    headers = dict(HEADERS_AUTH)
    headers["Authorization"] = f"Bearer {tokens['access_token']}"
    req = urllib.request.Request(url_me, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            me = json.loads(r.read().decode())
            print("Account Info:")
            print(json.dumps(me, indent=2))
    except Exception as e:
        print("Failed to query /device/me:", e)


def translate_chat_to_responses(req: dict) -> dict:
    """Translate OpenAI /v1/chat/completions body to OmniRush /v1/responses format."""
    messages = req.get("messages", [])
    model = req.get("model", "frontier")

    instructions = []
    input_items = []

    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, list):
            txt = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
        else:
            txt = str(content)

        if role == "system":
            instructions.append(txt)
        elif role == "user":
            input_items.append({
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": txt}],
            })
        elif role == "assistant":
            input_items.append({
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": txt}],
            })

    out = {
        "model": model,
        "input": input_items or [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]}],
        "stream": True,  # OmniRush strictly demands streamed responses
        "store": False,
    }
    if instructions:
        out["instructions"] = "\n\n".join(instructions)
    if req.get("max_tokens"):
        out["max_output_tokens"] = req["max_tokens"]

    return out


class OmniRushHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            tokens = load_tokens()
            self._send_json(200, {
                "ok": True,
                "service": "omnirush-bridge",
                "port": DEFAULT_PORT,
                "has_token": tokens is not None,
                "available_models": ["omnirush/frontier", "omnirush/flash", "omnirush/code"],
            })
            return

        if self.path in ("/v1/models", "/models"):
            models_list = [
                {"id": "OmniRush/frontier", "object": "model", "owned_by": "omnirush"},
                {"id": "OmniRush/flash", "object": "model", "owned_by": "omnirush"},
                {"id": "OmniRush/code", "object": "model", "owned_by": "omnirush"},
            ]
            self._send_json(200, {"object": "list", "data": models_list})
            return

        self._send_json(404, {"error": "Not Found"})

    def do_POST(self):
        if self.path not in ("/v1/chat/completions", "/chat/completions"):
            self._send_json(404, {"error": "Not Found"})
            return

        content_len = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_len).decode("utf-8", "ignore")

        try:
            req = json.loads(raw_body)
        except Exception as e:
            self._send_json(400, {"error": f"Invalid JSON: {e}"})
            return

        try:
            token = get_valid_token()
        except Exception as e:
            self._send_json(500, {"error": {"message": str(e), "type": "auth_error"}})
            return

        # Upstream call
        url = f"{OMNI_ORIGIN}/v1/responses"
        headers = dict(HEADERS_API)
        headers["Authorization"] = f"Bearer {token}"
        upstream_payload = translate_chat_to_responses(req)

        try:
            up_req = urllib.request.Request(url, data=json.dumps(upstream_payload).encode(), headers=headers)
            with urllib.request.urlopen(up_req, timeout=60) as resp:
                # Accumulate stream response
                chunks = []
                for line in resp:
                    l = line.decode("utf-8", "ignore").strip()
                    if l.startswith("data: ") and l != "data: [DONE]":
                        try:
                            d = json.loads(l[6:])
                            # extract delta or output text
                            txt = d.get("delta", {}).get("text") or d.get("text") or ""
                            if txt:
                                chunks.append(txt)
                        except Exception:
                            pass

                full_answer = "".join(chunks).strip() or "OK"

        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", "ignore")
            self._send_json(e.code, {"error": {"message": err_msg, "code": e.code}})
            return
        except Exception as e:
            self._send_json(500, {"error": {"message": str(e), "type": "upstream_error"}})
            return

        # Return OpenAI format
        chat_id = f"chatcmpl-omni-{uuid.uuid4().hex[:12]}"
        resp_obj = {
            "id": chat_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": req.get("model", "omnirush/frontier"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": full_answer},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": len(raw_body) // 4,
                "completion_tokens": len(full_answer) // 4,
                "total_tokens": (len(raw_body) + len(full_answer)) // 4,
            },
        }
        self._send_json(200, resp_obj)


def main():
    parser = argparse.ArgumentParser(description="OmniRush Bridge & Device Login")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("login", help="Run device authorization in browser")
    sub.add_parser("status", help="Check current token & user info")
    srv = sub.add_parser("serve", help="Run HTTP bridge server (:8795)")
    srv.add_argument("--port", type=int, default=DEFAULT_PORT)
    srv.add_argument("--host", default="127.0.0.1")

    args = parser.parse_args()
    if args.cmd == "login":
        do_login()
    elif args.cmd == "status":
        check_status()
    elif args.cmd == "serve" or not args.cmd:
        port = getattr(args, "port", DEFAULT_PORT)
        host = getattr(args, "host", "127.0.0.1")
        server = ThreadingHTTPServer((host, port), OmniRushHandler)
        log.info("OmniRush Bridge listening on http://%s:%d", host, port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.server_close()


if __name__ == "__main__":
    main()
