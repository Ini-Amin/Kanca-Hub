#!/usr/bin/env python3
"""qoder_bridge.py — OpenAI-compatible HTTP Bridge for Qoder AI Models.

Translates standard OpenAI /v1/chat/completions requests from 9Router or IDEs
into Qoder Agent Runtime calls using the zero-point PAT token.
Supports models:
  - DeepSeek-Flash (DeepSeek-1017, 0 credits)
  - Qwen3.8-Flash

Listening on http://127.0.0.1:8793
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_PORT = 8793
QODER_RUNTIME = "/opt/Qoder/resources/app.asar.unpacked/node_modules/@qoder-ai/qoder-agent-sdk/dist/_worker/qoder-worker-runtime.obf.mjs"
QODER_ACCOUNTS_FILE = Path.home() / ".config/auto-freecf/qoder_accounts.jsonl"
DEFAULT_PAT = os.environ.get("QODER_PERSONAL_ACCESS_TOKEN", "pt-VL0mNhk0XUQWX71Tm4MfqOdY_01a16e52-f539-77fd-9912-4f99e3b44098")

logging.basicConfig(
    level=logging.INFO,
    format="[qoder-bridge] %(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("qoder-bridge")


def get_pat_token() -> str:
    env_tok = os.environ.get("QODER_PERSONAL_ACCESS_TOKEN", "").strip()
    if env_tok:
        return env_tok
    return DEFAULT_PAT


def execute_qoder(model: str, prompt: str, timeout: int = 180) -> str:
    """Execute query through Qoder CLI runtime with the personal access token."""
    pat = get_pat_token()
    env = dict(os.environ)
    env["QODER_PERSONAL_ACCESS_TOKEN"] = pat

    # Map OpenAI model IDs to Qoder model names
    q_model = "DeepSeek-Flash"
    m_lower = model.lower()
    if "qwen" in m_lower:
        q_model = "Qwen3.8-Flash"
    elif "deepseek" in m_lower or "dfmodel" in m_lower or "1017" in m_lower:
        q_model = "DeepSeek-Flash"

    cmd = [
        "node",
        QODER_RUNTIME,
        "-m",
        q_model,
        "--bare",
        "--output-format",
        "json",
        "-p",
        prompt,
    ]

    log.info("Executing Qoder model=%s (query len=%d)...", q_model, len(prompt))
    t0 = time.time()
    res = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)
    elapsed = round(time.time() - t0, 2)

    if res.returncode != 0:
        log.error("Qoder CLI failed (%ss): %s", elapsed, res.stderr.strip()[:200])
        raise RuntimeError(f"Qoder execution error: {res.stderr.strip() or res.stdout.strip()}")

    out = res.stdout.strip()
    try:
        data = json.loads(out)
        res_text = data.get("result", "")
        log.info("Qoder replied in %ss (%d chars)", elapsed, len(res_text))
        return res_text
    except Exception:
        log.info("Qoder returned non-JSON text in %ss: %s", elapsed, out[:100])
        return out


class QoderBridgeHandler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send_json(200, {
                "ok": True,
                "service": "qoder-bridge",
                "default_model": "DeepSeek-Flash",
                "available_models": ["DeepSeek-Flash", "Qwen3.8-Flash"],
            })
            return

        if self.path in ("/v1/models", "/models"):
            models_data = [
                {"id": "qoder/DeepSeek-Flash", "object": "model", "owned_by": "qoder"},
                {"id": "qoder/Qwen3.8-Flash", "object": "model", "owned_by": "qoder"},
                {"id": "DeepSeek-Flash", "object": "model", "owned_by": "qoder"},
                {"id": "Qwen3.8-Flash", "object": "model", "owned_by": "qoder"},
            ]
            self._send_json(200, {"object": "list", "data": models_data})
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

        model = req.get("model", "DeepSeek-Flash")
        messages = req.get("messages", [])
        stream = bool(req.get("stream", False))

        # Flatten conversation to prompt
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

        full_prompt = "\n".join(prompt_parts) or "hi"

        try:
            answer = execute_qoder(model, full_prompt)
        except Exception as e:
            self._send_json(500, {"error": {"message": str(e), "type": "qoder_bridge_error"}})
            return

        chat_id = f"chatcmpl-qoder-{uuid.uuid4().hex[:12]}"
        created_ts = int(time.time())

        if stream:
            # Stream SSE format
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()

            # Chunk 1: Role
            c1 = {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            self.wfile.write(f"data: {json.dumps(c1)}\n\n".encode("utf-8"))

            # Chunk 2: Content
            c2 = {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": model,
                "choices": [{"index": 0, "delta": {"content": answer}, "finish_reason": None}],
            }
            self.wfile.write(f"data: {json.dumps(c2)}\n\n".encode("utf-8"))

            # Chunk 3: Finish
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
            # Standard JSON
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
    parser = argparse.ArgumentParser(description="Qoder AI Bridge Server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to listen on")
    args = parser.parse_args()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), QoderBridgeHandler)
    log.info("Qoder Bridge listening on http://127.0.0.1:%d", args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Stopping Qoder Bridge...")


if __name__ == "__main__":
    main()
