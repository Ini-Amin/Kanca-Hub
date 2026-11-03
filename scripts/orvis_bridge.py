#!/usr/bin/env python3
"""scripts/orvis_bridge.py — OpenAI-compatible local HTTP bridge for Orvis AI (:8796).

Pure standard library + websockets.
Exposes Orvis AI models as an OpenAI-compatible /v1/chat/completions endpoint:
  - orvis/claude-sonnet-4
  - orvis/claude-opus-4
  - orvis/gpt-4o
  - orvis/o3-mini
  - orvis/gemini-2.5-flash
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import orvis_client

PORT = 8796
HOST = "127.0.0.1"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("orvis-bridge")


class OrvisBridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Terse logging
        logger.info("%s - %s", self.address_string(), format % args)

    def _send_json(self, data: dict, status: int = 200) -> None:
        raw = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            accounts = orvis_client.load_accounts()
            tot_bal = sum(a.get("balance", 0) for a in accounts)
            self._send_json({
                "status": "ok",
                "service": "orvis-bridge",
                "port": PORT,
                "accounts_count": len(accounts),
                "total_credits": tot_bal,
                "models": list(orvis_client.MODEL_MAP.keys()),
            })
        elif self.path in ("/v1/models", "/models"):
            now = int(time.time())
            data = []
            for m in orvis_client.MODEL_MAP.keys():
                if m != "default":
                    data.append({
                        "id": f"orvis/{m}",
                        "object": "model",
                        "created": now,
                        "owned_by": "orvis",
                    })
            self._send_json({"object": "list", "data": data})
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        if self.path in ("/v1/chat/completions", "/chat/completions"):
            length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(length).decode("utf-8")
            try:
                body = json.loads(raw_body)
            except Exception:
                self._send_json({"error": {"message": "Invalid JSON"}}, status=400)
                return

            model_req = body.get("model", "orvis/gemini-2.5-flash")
            model_name = model_req.replace("orvis/", "").strip()
            messages = body.get("messages", [])

            prompt_parts = []
            for msg in messages:
                role = msg.get("role", "user")
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                if role == "system":
                    prompt_parts.append(f"[System Instruction: {content}]")
                elif role == "user":
                    prompt_parts.append(f"User: {content}")
                elif role == "assistant":
                    prompt_parts.append(f"Assistant: {content}")
            full_prompt = "\n\n".join(prompt_parts) if prompt_parts else "Hello"

            cid = f"chatcmpl-orvis-{int(time.time()*1000)}"
            created = int(time.time())
            is_stream = bool(body.get("stream", False))
            if is_stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()

                def on_delta(delta: str):
                    chunk = {
                        "id": cid,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model_req,
                        "choices": [{
                            "index": 0,
                            "delta": {"content": delta},
                            "finish_reason": None,
                        }],
                    }
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
                    self.wfile.flush()

                try:
                    reply = asyncio.run(orvis_client.chat_turn(full_prompt, model_name=model_name, on_delta=on_delta))
                    done_chunk = {
                        "id": cid,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model_req,
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    }
                    self.wfile.write(f"data: {json.dumps(done_chunk)}\n\ndata: [DONE]\n\n".encode("utf-8"))
                    self.wfile.flush()
                except Exception as e:
                    logger.error("Chat turn error: %s", e)
                    err_chunk = {"error": {"message": str(e)}}
                    self.wfile.write(f"data: {json.dumps(err_chunk)}\n\n".encode("utf-8"))
                    self.wfile.flush()
                return

            try:
                reply = asyncio.run(orvis_client.chat_turn(full_prompt, model_name=model_name))
                self._send_json({
                    "id": cid,
                    "object": "chat.completion",
                    "created": created,
                    "model": model_req,
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": reply},
                        "finish_reason": "stop",
                    }],
                    "usage": {
                        "prompt_tokens": len(full_prompt) // 4,
                        "completion_tokens": len(reply) // 4,
                        "total_tokens": (len(full_prompt) + len(reply)) // 4,
                    },
                })
            except Exception as e:
                logger.error("Chat turn error: %s", e)
                self._send_json({"error": {"message": str(e)}}, status=500)
        else:
            self._send_json({"error": "not found"}, status=404)


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), OrvisBridgeHandler)
    logger.info("Serving Orvis AI bridge on http://%s:%d", HOST, PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
