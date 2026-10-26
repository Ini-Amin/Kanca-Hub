#!/usr/bin/env python3
"""codebuddy_bridge.py — OpenAI-compatible HTTP Bridge for Tencent CodeBuddy AI Models.

Translates standard OpenAI /v1/chat/completions requests from 9Router or IDEs
into CodeBuddy CLI calls using CODEBUDDY_AUTH_TOKEN extracted from 9Router SQLite.
Bypasses channel verification checks.

Supported models:
  - deepseek-v4.1-flash
  - glm-5.3
  - glm-5.2
  - gpt-6-astra
  - gpt-5.6-sol
  - kimi-k3

Listening on http://127.0.0.1:8794
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_PORT = 8794
NINE_ROUTER_DB = Path.home() / ".9router/db/data.sqlite"
CLI_BIN = "/home/amen/.npm-global/bin/codebuddy"
CACHE_TOKEN_FILE = Path.home() / ".config/auto-freecf/codebuddy_token.txt"

logging.basicConfig(
    level=logging.INFO,
    format="[codebuddy-bridge] %(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("codebuddy-bridge")

_WORKING_TOKENS: list[str] = []


def load_codebuddy_tokens() -> list[str]:
    """Load valid CodeBuddy access tokens from 9Router DB and disk cache."""
    global _WORKING_TOKENS
    if _WORKING_TOKENS:
        return _WORKING_TOKENS

    tokens = []
    if CACHE_TOKEN_FILE.exists():
        cached = CACHE_TOKEN_FILE.read_text().strip()
        if cached:
            tokens.append(cached)

    if NINE_ROUTER_DB.exists():
        try:
            con = sqlite3.connect(str(NINE_ROUTER_DB))
            rows = con.cursor().execute(
                "SELECT id, data FROM providerConnections WHERE provider='codebuddy-intl'"
            ).fetchall()
            for row_id, data_str in rows:
                try:
                    d = json.loads(data_str)
                    tok = d.get("accessToken", "").strip()
                    if tok and tok not in tokens:
                        tokens.append(tok)
                except Exception:
                    pass
            con.close()
        except Exception as e:
            log.error("Failed to read 9Router DB: %s", e)

    _WORKING_TOKENS = tokens
    return tokens


def get_token(preferred_index: int = 0) -> str:
    tokens = load_codebuddy_tokens()
    if not tokens:
        env_tok = os.environ.get("CODEBUDDY_AUTH_TOKEN", "").strip()
        if env_tok:
            return env_tok
        raise RuntimeError("No active CodeBuddy token found")
    return tokens[preferred_index % len(tokens)]


def execute_codebuddy(model: str, prompt: str, timeout: int = 120) -> str:
    """Execute prompt through CodeBuddy CLI with verified auth token."""
    tokens = load_codebuddy_tokens()
    last_err = ""

    # Map request model name
    m_lower = model.lower()
    cb_model = "deepseek-v4.1-flash"
    if "hy3" in m_lower or "hunyuan" in m_lower:
        cb_model = "hy3"
    elif "glm-5.3" in m_lower:
        cb_model = "glm-5.3"
    elif "glm-5.2" in m_lower:
        cb_model = "glm-5.2"
    elif "astra" in m_lower or "gpt-6" in m_lower:
        cb_model = "gpt-6-astra"
    elif "sol" in m_lower or "gpt-5.6" in m_lower:
        cb_model = "gpt-5.6-sol"
    elif "kimi" in m_lower:
        cb_model = "kimi-k3"
    elif "1017" in m_lower or "deepseek" in m_lower:
        cb_model = "deepseek-v4.1-flash"
    for idx, token in enumerate(tokens):
        env = dict(os.environ)
        env["CODEBUDDY_AUTH_TOKEN"] = token
        env["PATH"] = f"/home/amen/.npm-global/bin:{env.get('PATH', '')}"

        cmd = [
            CLI_BIN,
            "--print",
            "--model",
            cb_model,
            prompt,
        ]

        log.info("Executing CodeBuddy (token %d/%d, model=%s, prompt len=%d)...",
                 idx + 1, len(tokens), cb_model, len(prompt))
        t0 = time.time()
        res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
        elapsed = round(time.time() - t0, 2)

        out = res.stdout.strip()
        err = res.stderr.strip()

        if res.returncode == 0 and out:
            log.info("CodeBuddy replied in %ss (%d chars)", elapsed, len(out))
            # Cache this winning token to file
            try:
                CACHE_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
                CACHE_TOKEN_FILE.write_text(token)
            except Exception:
                pass
            return out

        last_err = err or out or f"Exit code {res.returncode}"
        log.warning("Token %d failed (%ss): %s; trying next token...", idx + 1, elapsed, last_err[:120])

    raise RuntimeError(f"All CodeBuddy tokens failed. Last error: {last_err}")


class CodeBuddyBridgeHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            tokens = load_codebuddy_tokens()
            self._send_json(200, {
                "ok": True,
                "service": "codebuddy-bridge",
                "port": DEFAULT_PORT,
                "tokens_loaded": len(tokens),
                "available_models": [
                    "deepseek-v4.1-flash",
                    "hy3",
                    "glm-5.3",
                    "glm-5.2",
                    "gpt-6-astra",
                    "gpt-5.6-sol",
                    "kimi-k3",
                ],
            })
            return

        if self.path in ("/v1/models", "/models"):
            models_list = [
                {"id": "CodeBuddy/deepseek-v4.1-flash", "object": "model", "owned_by": "tencent-codebuddy"},
                {"id": "CodeBuddy/deepseek-1017", "object": "model", "owned_by": "tencent-codebuddy"},
                {"id": "CodeBuddy/hy3", "object": "model", "owned_by": "tencent-codebuddy"},
                {"id": "CodeBuddy/glm-5.3", "object": "model", "owned_by": "tencent-codebuddy"},
                {"id": "CodeBuddy/glm-5.2", "object": "model", "owned_by": "tencent-codebuddy"},
                {"id": "CodeBuddy/gpt-6-astra", "object": "model", "owned_by": "tencent-codebuddy"},
                {"id": "CodeBuddy/gpt-5.6-sol", "object": "model", "owned_by": "tencent-codebuddy"},
                {"id": "CodeBuddy/kimi-k3", "object": "model", "owned_by": "tencent-codebuddy"},
                {"id": "deepseek-v4.1-flash", "object": "model", "owned_by": "tencent-codebuddy"},
                {"id": "deepseek-1017", "object": "model", "owned_by": "tencent-codebuddy"},
                {"id": "hy3", "object": "model", "owned_by": "tencent-codebuddy"},
                {"id": "glm-5.3", "object": "model", "owned_by": "tencent-codebuddy"},
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

        model = req.get("model", "deepseek-v4.1-flash")
        messages = req.get("messages", [])
        stream = bool(req.get("stream", False))

        prompt_parts = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if isinstance(content, list):
                txt = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
            else:
                txt = str(content)
            if role == "system":
                prompt_parts.append(f"[System: {txt}]")
            elif role == "user":
                prompt_parts.append(f"User: {txt}")
            elif role == "assistant":
                prompt_parts.append(f"Assistant: {txt}")

        full_prompt = "\n".join(prompt_parts) or "Hello"

        try:
            answer = execute_codebuddy(model, full_prompt)
        except Exception as e:
            self._send_json(500, {"error": {"message": str(e), "type": "codebuddy_bridge_error"}})
            return

        chat_id = f"chatcmpl-codebuddy-{uuid.uuid4().hex[:12]}"
        created_ts = int(time.time())

        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            c1 = {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            self.wfile.write(f"data: {json.dumps(c1)}\n\n".encode("utf-8"))

            c2 = {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": model,
                "choices": [{"index": 0, "delta": {"content": answer}, "finish_reason": None}],
            }
            self.wfile.write(f"data: {json.dumps(c2)}\n\n".encode("utf-8"))

            c3 = {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            self.wfile.write(f"data: {json.dumps(c3)}\n\n".encode("utf-8"))
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        else:
            resp = {
                "id": chat_id,
                "object": "chat.completion",
                "created": created_ts,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": answer},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": len(full_prompt) // 4,
                    "completion_tokens": len(answer) // 4,
                    "total_tokens": (len(full_prompt) + len(answer)) // 4,
                },
            }
            self._send_json(200, resp)


def main():
    parser = argparse.ArgumentParser(description="CodeBuddy OpenAI Bridge")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to listen on (default: 8794)")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1)")
    args = parser.parse_args()

    # Pre-cache known working token
    con = sqlite3.connect(str(NINE_ROUTER_DB))
    r = con.cursor().execute("SELECT data FROM providerConnections WHERE id='2da3ea8e-782a-4cb9-869d-12a74d681fa2'").fetchone()
    if r:
        tok = json.loads(r[0]).get("accessToken")
        if tok:
            CACHE_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            CACHE_TOKEN_FILE.write_text(tok)
    con.close()

    server = ThreadingHTTPServer((args.host, args.port), CodeBuddyBridgeHandler)
    log.info("CodeBuddy Bridge running on http://%s:%d", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down CodeBuddy Bridge...")
        server.server_close()


if __name__ == "__main__":
    main()
