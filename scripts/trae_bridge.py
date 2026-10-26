#!/usr/bin/env python3
"""trae_bridge.py — OpenAI-compatible Bridge for ByteDance Trae AI Models.

Translates standard OpenAI /v1/chat/completions requests from 9Router into
ByteDance Trae AI chat stream requests (https://coresg-normal.trae.ai/api/ide/v1/chat).

Supported models:
  - Trae/claude-3-5-sonnet
  - Trae/claude-sonnet-4
  - Trae/gpt-4o
  - Trae/gpt-4o-mini
  - Trae/deepseek-v3

Listening on http://127.0.0.1:8796
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_PORT = 8796
CONFIG_DIR = Path.home() / ".config" / "auto-freecf"
TOKEN_FILE = CONFIG_DIR / "trae_token.txt"

TRAE_ORIGIN = "https://coresg-normal.trae.ai"
CHAT_ENDPOINT = f"{TRAE_ORIGIN}/api/ide/v1/chat"

logging.basicConfig(
    level=logging.INFO,
    format="[trae-bridge] %(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("trae-bridge")


def load_trae_token() -> str:
    if TOKEN_FILE.exists():
        tok = TOKEN_FILE.read_text().strip()
        if tok:
            return tok
    env_tok = os.environ.get("TRAE_AUTH_TOKEN", "").strip()
    if env_tok:
        return env_tok
    return ""


def save_trae_token(token: str):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token.strip())
    log.info("Saved Trae session token to %s", TOKEN_FILE)


def map_model_name(req_model: str) -> str:
    m = req_model.lower().replace("trae/", "").strip()
    if "sonnet" in m or "3.5" in m or "3-5" in m:
        return "claude-3-5-sonnet"
    if "gpt-4o-mini" in m:
        return "gpt-4o-mini"
    if "gpt-4" in m or "4o" in m:
        return "gpt-4o"
    if "deepseek" in m or "v3" in m:
        return "deepseek-v3"
    return "claude-3-5-sonnet"


def execute_trae_chat(model: str, prompt: str, timeout: int = 120) -> str:
    token = load_trae_token()
    trae_model = map_model_name(model)

    payload = {
        "user_input": prompt,
        "model_name": trae_model,
        "intent_name": "chat",
        "chat_id": f"chat_{uuid.uuid4().hex[:12]}",
        "variables": {},
        "chat_history": [],
    }

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Trae/2.3.93439 Chrome/128.0.6613.186 Electron/32.2.6",
        "Accept": "text/event-stream, application/json",
        "Origin": "https://www.trae.ai",
        "Referer": "https://www.trae.ai/",
    }

    if token:
        if token.startswith("sessionid="):
            headers["Cookie"] = token
        else:
            headers["Authorization"] = f"Bearer {token}"
            headers["Cookie"] = f"sessionid={token}; sessionid_ss={token}"

    req = urllib.request.Request(CHAT_ENDPOINT, data=json.dumps(payload).encode(), headers=headers)
    log.info("Sending request to Trae API (model=%s, prompt_len=%d)...", trae_model, len(prompt))

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        chunks = []
        for line in resp:
            l = line.decode("utf-8", "ignore").strip()
            if l.startswith("data: "):
                raw_data = l[6:].strip()
                try:
                    d = json.loads(raw_data)
                    # Check for text or delta in Trae SSE frame
                    txt = d.get("text") or d.get("content") or d.get("message") or ""
                    if txt:
                        chunks.append(txt)
                except Exception:
                    pass

        return "".join(chunks).strip() or "(Empty response from Trae)"


class TraeHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            tok = load_trae_token()
            self._send_json(200, {
                "ok": True,
                "service": "trae-bridge",
                "port": DEFAULT_PORT,
                "has_token": bool(tok),
                "available_models": [
                    "claude-3-5-sonnet",
                    "claude-sonnet-4",
                    "gpt-4o",
                    "gpt-4o-mini",
                    "deepseek-v3",
                ],
            })
            return

        if self.path in ("/v1/models", "/models"):
            models_list = [
                {"id": "Trae/claude-3-5-sonnet", "object": "model", "owned_by": "bytedance-trae"},
                {"id": "Trae/gpt-4o", "object": "model", "owned_by": "bytedance-trae"},
                {"id": "Trae/deepseek-v3", "object": "model", "owned_by": "bytedance-trae"},
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

        model = req.get("model", "claude-3-5-sonnet")
        messages = req.get("messages", [])

        # Flatten prompt
        parts = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if isinstance(content, list):
                txt = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
            else:
                txt = str(content)
            if role == "system":
                parts.append(f"[System Instruction: {txt}]")
            elif role == "user":
                parts.append(f"User: {txt}")
            elif role == "assistant":
                parts.append(f"Assistant: {txt}")

        prompt = "\n".join(parts) or "Hello"

        try:
            answer = execute_trae_chat(model, prompt)
        except urllib.error.HTTPError as e:
            self._send_json(e.code, {"error": {"message": e.read().decode("utf-8", "ignore"), "code": e.code}})
            return
        except Exception as e:
            self._send_json(500, {"error": {"message": str(e), "type": "trae_bridge_error"}})
            return

        chat_id = f"chatcmpl-trae-{uuid.uuid4().hex[:12]}"
        resp = {
            "id": chat_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": answer},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": len(prompt) // 4,
                "completion_tokens": len(answer) // 4,
                "total_tokens": (len(prompt) + len(answer)) // 4,
            },
        }
        self._send_json(200, resp)


def main():
    parser = argparse.ArgumentParser(description="Trae AI OpenAI Bridge")
    sub = parser.add_subparsers(dest="cmd")

    tok = sub.add_parser("set-token", help="Save Trae sessionid cookie / token")
    tok.add_argument("token", help="Passport sessionid cookie or bearer token")

    srv = sub.add_parser("serve", help="Run HTTP bridge (:8796)")
    srv.add_argument("--port", type=int, default=DEFAULT_PORT)
    srv.add_argument("--host", default="127.0.0.1")

    args = parser.parse_args()
    if args.cmd == "set-token":
        save_trae_token(args.token)
    elif args.cmd == "serve" or not args.cmd:
        port = getattr(args, "port", DEFAULT_PORT)
        host = getattr(args, "host", "127.0.0.1")
        server = ThreadingHTTPServer((host, port), TraeHandler)
        log.info("Trae Bridge listening on http://%s:%d", host, port)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.server_close()


if __name__ == "__main__":
    main()
