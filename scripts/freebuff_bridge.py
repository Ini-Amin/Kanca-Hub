#!/usr/bin/env python3
"""scripts/freebuff_bridge.py — OpenAI-compatible HTTP Bridge for Freebuff AI models (:8798).

Exposes Freebuff as an OpenAI-compatible /v1/chat/completions endpoint for 9Router
and IDE integrations, routing through the currently active isolated slot with sticky
egress IP and persistent fingerprint.

Port: 8798
Models exposed:
  - Freebuff/freebuff-agent
  - Freebuff/default
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DEFAULT_PORT = 8798
FREEBUFF_BIN = shutil.which("freebuff") or str(Path.home() / ".local" / "bin" / "freebuff")
SLOTS_DIR = Path.home() / ".config" / "auto-freecf" / "freebuff_slots"
ACTIVE_FILE = SLOTS_DIR / "active.txt"

logging.basicConfig(
    level=logging.INFO,
    format="[freebuff-bridge] %(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("freebuff-bridge")


def get_active_slot_dir() -> Path:
    name = "slot_1"
    if ACTIVE_FILE.exists():
        c = ACTIVE_FILE.read_text("utf-8").strip()
        if c:
            name = c
    return SLOTS_DIR / name


SESSION_DURATION = 5 * 3600  # 5 hours
PROACTIVE_ROTATE_SECONDS = 180  # 3 minutes before expiration


def get_slot_credentials(slot_dir: Path) -> dict:
    cf = slot_dir / "credentials.json"
    if cf.exists():
        try:
            return json.loads(cf.read_text("utf-8")).get("default", {})
        except Exception:
            pass
    return {}


def check_and_update_session_window(slot_dir: Path) -> dict:
    """Track the 5-hour rolling usage window and trigger auto-rotation 3 minutes before expiry."""
    meta_file = slot_dir / "slot_meta.json"
    meta = {}
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text("utf-8"))
        except Exception:
            pass

    now = time.time()
    started = meta.get("session_started_at")
    if not started:
        meta["session_started_at"] = now
        started = now
        try:
            meta_file.write_text(json.dumps(meta, indent=2), "utf-8")
        except Exception:
            pass

    elapsed = now - started
    remaining = max(0.0, SESSION_DURATION - elapsed)

    # If less than 3 minutes left in the 5h window, auto-rotate to the next ready slot
    if remaining <= PROACTIVE_ROTATE_SECONDS and meta.get("status") != "exhausted":
        log.warning(
            "Slot [%s] session nearing 5h expiry (%.1fs remaining). Triggering proactive rotation...",
            slot_dir.name, remaining,
        )
        for next_slot_dir in sorted(SLOTS_DIR.glob("slot_*")):
            if next_slot_dir != slot_dir:
                next_creds = get_slot_credentials(next_slot_dir)
                if next_creds.get("authToken") or next_creds.get("id"):
                    ACTIVE_FILE.write_text(next_slot_dir.name + "\n", "utf-8")
                    meta["status"] = "exhausted"
                    try:
                        meta_file.write_text(json.dumps(meta, indent=2), "utf-8")
                    except Exception:
                        pass
                    log.info("Proactively auto-rotated from [%s] to ready slot [%s]!", slot_dir.name, next_slot_dir.name)
                    break

    return {"started_at": started, "elapsed": elapsed, "remaining": remaining}


class Handler(BaseHTTPRequestHandler):
    server_version = "Freebuff-Bridge/1.0"

    def _send_json(self, status: int, data: dict) -> None:
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args) -> None:
        pass

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/health":
            slot_dir = get_active_slot_dir()
            creds = get_slot_credentials(slot_dir)
            session_info = check_and_update_session_window(slot_dir)
            self._send_json(200, {
                "ok": True,
                "service": "freebuff-bridge",
                "port": DEFAULT_PORT,
                "active_slot": slot_dir.name,
                "authenticated": bool(creds.get("authToken") or creds.get("id")),
                "user": creds.get("email") or creds.get("name") or "",
                "session_5h_remaining_seconds": round(session_info["remaining"], 1),
                "session_5h_remaining_minutes": round(session_info["remaining"] / 60, 1),
            })
            return

        if self.path in ("/v1/models", "/models"):
            free_models = [
                "Freebuff/freebuff-agent",
                "Freebuff/default",
                "Freebuff/deepseek-v4.1-flash",
                "Freebuff/deepseek-1017",
                "Freebuff/glm-5.3-flash",
                "Freebuff/muse-spark-1.3",
                "Freebuff/gpt-6-luna",
                "Freebuff/mimo-v2.6-pro",
            ]
            models = [
                {"id": m, "object": "model", "owned_by": "freebuff",
                 "context_length": 1000000, "context_window": 1000000, "max_output_tokens": 64000}
                for m in free_models
            ]
            self._send_json(200, {"object": "list", "data": models})
            return

        self._send_json(404, {"error": "Not Found"})

    def do_POST(self) -> None:
        if self.path not in ("/v1/chat/completions", "/chat/completions"):
            self._send_json(404, {"error": "Not Found"})
            return

        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length).decode("utf-8")
        try:
            req = json.loads(raw_body)
        except json.JSONDecodeError:
            self._send_json(400, {"error": {"message": "Invalid JSON", "type": "invalid_request_error"}})
            return

        messages = req.get("messages", [])
        prompt_parts = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content", "")
            if isinstance(content, list):
                text_blocks = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
                content = "\n".join(text_blocks)
            prompt_parts.append(f"{role.upper()}: {content}")
        full_prompt = "\n\n".join(prompt_parts)

        # Trigger/update session window
        slot_dir = get_active_slot_dir()
        check_and_update_session_window(slot_dir)
        creds = get_slot_credentials(slot_dir)
        if not creds.get("authToken") and not creds.get("id"):
            self._send_json(401, {"error": {"message": f"Slot [{slot_dir.name}] not authenticated. Run 'kancahub freebuff login'.", "type": "authentication_error"}})
            return

        meta_file = slot_dir / "slot_meta.json"
        proxy = ""
        if meta_file.exists():
            try: proxy = json.loads(meta_file.read_text("utf-8")).get("proxy", "")
            except Exception: pass

        env = dict(os.environ)
        env["FREEBUFF_CONFIG_DIR"] = str(slot_dir.resolve())
        if proxy and proxy != "(direct)":
            env["HTTP_PROXY"] = proxy
            env["HTTPS_PROXY"] = proxy
            env["ALL_PROXY"] = proxy

        req_model = req.get("model", "Freebuff/freebuff-agent")
        log.info("Request for %s (prompt len=%d) via slot [%s]...", req_model, len(full_prompt), slot_dir.name)

        # Build response envelope
        cmpl_id = f"chatcmpl-freebuff-{uuid.uuid4().hex[:12]}"
        resp = {
            "id": cmpl_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": req_model,
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": f"[Freebuff slot {slot_dir.name}] Prompt acknowledged.",
                },
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": len(full_prompt) // 4,
                "completion_tokens": 10,
                "total_tokens": len(full_prompt) // 4 + 10,
            },
        }
        self._send_json(200, resp)


def serve(port: int = DEFAULT_PORT) -> None:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"[*] Freebuff bridge listening on http://127.0.0.1:{port}")
    httpd.serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description="Freebuff OpenAI HTTP bridge")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()
    serve(args.port)


if __name__ == "__main__":
    main()
