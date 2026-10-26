#!/usr/bin/env python3
"""AutoClaw → OpenAI-compatible bridge on 127.0.0.1:8797.

Translates 9Router /v1/chat/completions into AutoClaw's managed model gateway
(https://autoglm-api.autoglm.ai/autoclaw-proxy/proxy/autoclaw/chat/completions).
Token source priority: 18432/get_token (running app) → vault decrypt → jsonl.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from autoclaw_client import (  # noqa: E402
    APP_ID, APP_KEY, BASE, DEFAULT_UA, AutoClawClient, load_app_credentials,
    load_accounts, save_account,
)

MODELS = ["zai_auto", "zai_auto-fast", "zaicoding_glm-5.3", "zai_glm-5.3-flash"]
PORT = 8797


def gateway_headers(token: str, model: str) -> dict[str, str]:
    ts = str(int(time.time()))
    return {
        "Content-Type": "application/json",
        "User-Agent": DEFAULT_UA,
        "X-Version": "2.0.3",
        "X-Tm": "linux",
        "X-Product": "autoclaw",
        "X-Channel": "official",
        "X-Lang": "en-US",
        "X-Auth-Appid": APP_ID,
        "X-Auth-TimeStamp": ts,
        "X-Auth-Sign": __import__("hashlib").md5(f"{APP_ID}&{ts}&{APP_KEY}".encode()).hexdigest(),
        "X-Authorization": f"Bearer {token}",
        "X-Trace-Id": uuid.uuid4().hex,
        "X-Request-Id": uuid.uuid4().hex,
        "X-Request-Model": model,
        "X-Client-Type": "pc",
        "x_trace_id": "autoclaw-desktop",
    }


def pick_token() -> tuple[str | None, str | None]:
    """(access_token, refresh_token) — app first, then the local ledger."""
    creds = load_app_credentials()
    if creds and creds.get("access_token"):
        return creds["access_token"], creds.get("refresh_token")
    for acc in reversed(load_accounts()):
        if acc.get("access_token"):
            return acc["access_token"], acc.get("refresh_token")
    return None, None


def try_refresh(client: AutoClawClient, refresh_tok: str | None) -> str | None:
    if not refresh_tok:
        return None
    res = client.refresh_token(refresh_tok)
    if res.get("code") == 0:
        return (res.get("data") or {}).get("access_token")
    return None


def call_gateway(model: str, body: dict, token: str) -> tuple[int, bytes]:
    payload = dict(body)
    payload["model"] = model
    req = urllib.request.Request(
        f"{BASE}/autoclaw-proxy/proxy/autoclaw/chat/completions",
        data=json.dumps(payload).encode(),
        headers=gateway_headers(token, model),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _json(self, status: int, obj: dict) -> None:
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            token, _ = pick_token()
            info: dict = {"ok": bool(token), "models": MODELS}
            if token:
                try:
                    wallets = AutoClawClient().get_wallets(token)
                    d = wallets.get("data") or {}
                    info["total_balance"] = d.get("total_balance")
                except Exception as e:
                    info["wallet_error"] = str(e)
            return self._json(200, info)
        if self.path == "/v1/models":
            return self._json(200, {"object": "list", "data": [
                {"id": m, "object": "model", "owned_by": "autoclaw"} for m in MODELS]})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            return self._json(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as e:
            return self._json(400, {"error": f"bad request body: {e}"})

        asked = body.get("model") or MODELS[0]
        model = next((m for m in MODELS if asked.endswith(m)), None)
        if not model:
            return self._json(400, {"error": f"unknown model {asked!r}; available: {MODELS}"})

        client = AutoClawClient()
        token, refresh_tok = pick_token()
        if not token:
            return self._json(503, {"error": "no AutoClaw token; log in to the app first"})

        status, raw = call_gateway(model, body, token)
        if status == 401:
            fresh = try_refresh(client, refresh_tok)
            if fresh:
                token = fresh
                status, raw = call_gateway(model, body, token)
        if status >= 400:
            return self._json(status, {"error": raw.decode("utf-8", "replace")[:800]})
        if body.get("stream"):
            self.send_response(status)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for chunk in (raw[i:i + 8192] for i in range(0, len(raw), 8192)):
                self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
            self.wfile.write(b"0\r\n\r\n")
            return
        self._json(status, json.loads(raw.decode("utf-8")))

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    print(f"autoclaw-bridge listening on 127.0.0.1:{PORT}, models: {MODELS}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
