#!/usr/bin/env python3
"""OpenCode Local Bridge: expose OpenCode background service as OpenAI-compatible API.

Reverse-engineered from OpenCode Desktop (same architecture as NEO bridge in Image #1):
1. Reads daemon URL and secret token from ~/.local/state/opencode/service.json.
   If not running, auto-spawns `opencode serve --service`.
2. Connects using HTTP Basic Auth: "opencode:<password>".
3. Exposes OpenAI-compatible server on 127.0.0.1:8791:
   - GET  /v1/models
   - POST /v1/chat/completions
4. Translates OpenAI chat messages into OpenCode session/prompt/wait/message lifecycle.
5. Supports account token rotation via ~/.config/auto-freecf/opencode_accounts.json.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

SERVICE_JSON = Path.home() / ".local/state/opencode/service.json"
AUTH_JSON = Path.home() / ".local/share/opencode/auth.json"
ACCOUNTS_JSON = Path.home() / ".config/auto-freecf/opencode_accounts.json"
DEFAULT_PORT = 8791

FREE_MODELS = [
    "glm-5.3-flash-free",
    "deepseek-v4.1-flash-free",
    "mimo-v2.6-flash-free",
    "nemotron-3.5-super-vl-free",
    "nemotron-3.5-lightning-free",
    "nemotron-3-ultra-free",
    "flint-40b-free",
    "beam-free",
    "big-pickle",
]


@dataclass
class OpenCodeServiceInfo:
    id: str
    version: str
    url: str
    pid: int
    password: str

    @property
    def auth_header(self) -> str:
        creds = f"opencode:{self.password}"
        return "Basic " + base64.b64encode(creds.encode()).decode()


def get_service_info(*, auto_spawn: bool = True) -> OpenCodeServiceInfo:
    if SERVICE_JSON.is_file():
        try:
            d = json.loads(SERVICE_JSON.read_text())
            # Check liveness
            info = OpenCodeServiceInfo(
                id=d.get("id", ""),
                version=d.get("version", ""),
                url=d.get("url", ""),
                pid=int(d.get("pid", 0)),
                password=d.get("password", ""),
            )
            if _ping_service(info):
                return info
        except Exception:
            pass

    if not auto_spawn:
        sys.exit("OpenCode background service is not running and auto_spawn=False")

    binary = shutil.which("opencode") or str(Path.home() / ".opencode/bin/opencode")
    if not Path(binary).is_file():
        sys.exit(f"opencode binary not found at {binary}")

    print("[bridge] Spawning opencode serve --service...")
    subprocess.Popen(
        [binary, "serve", "--service"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(30):
        time.sleep(0.5)
        if SERVICE_JSON.is_file():
            try:
                d = json.loads(SERVICE_JSON.read_text())
                info = OpenCodeServiceInfo(
                    id=d.get("id", ""),
                    version=d.get("version", ""),
                    url=d.get("url", ""),
                    pid=int(d.get("pid", 0)),
                    password=d.get("password", ""),
                )
                if _ping_service(info):
                    print(f"[bridge] Connected to OpenCode daemon pid={info.pid} on {info.url}")
                    return info
            except Exception:
                pass
    sys.exit("Timed out waiting for opencode serve --service to start")


def _ping_service(info: OpenCodeServiceInfo) -> bool:
    try:
        req = urllib.request.Request(
            f"{info.url}/api/info",
            headers={"Authorization": info.auth_header},
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def _daemon_request(info: OpenCodeServiceInfo, path: str, method: str = "GET", data: Any = None) -> tuple[int, Any]:
    payload = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        f"{info.url}{path}",
        data=payload,
        method=method,
        headers={
            "Authorization": info.auth_header,
            "Content-Type": "application/json",
            "User-Agent": "opencode-bridge/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode()
            if not body:
                return resp.status, {}
            try:
                return resp.status, json.loads(body)
            except Exception:
                return resp.status, body
    except urllib.error.HTTPError as e:
        err_body = e.read().decode()
        try:
            return e.code, json.loads(err_body)
        except Exception:
            return e.code, err_body


def chat_generate(model_name: str, prompt: str, *, system_prompt: str = "") -> dict:
    info = get_service_info()
    model_id = model_name.split("/")[-1]

    # 1. Create a session
    st, sess = _daemon_request(info, "/api/session", "POST", {"title": f"bridge-{secrets.token_hex(4)}"})
    if st != 200 or not isinstance(sess, dict) or "data" not in sess:
        raise RuntimeError(f"Failed to create session (HTTP {st}): {sess}")
    sid = sess["data"]["id"]

    try:
        # 2. Set model
        st, _ = _daemon_request(
            info,
            f"/api/session/{sid}/model",
            "POST",
            {"model": {"providerID": "opencode", "id": model_id}},
        )

        full_prompt = prompt
        if system_prompt:
            full_prompt = f"[System Context]\n{system_prompt}\n\n[User Prompt]\n{prompt}"

        # 3. Send prompt
        msg_id = "msg_" + secrets.token_hex(12)
        st, pres = _daemon_request(
            info,
            f"/api/session/{sid}/prompt",
            "POST",
            {
                "id": msg_id,
                "text": full_prompt,
                "files": [],
                "delivery": "steer",
            },
        )
        if st != 200:
            raise RuntimeError(f"Prompt failed (HTTP {st}): {pres}")

        # 4. Wait for generation
        _daemon_request(info, f"/api/experimental/session/{sid}/wait", "POST", {})

        # 5. Fetch message
        st, msgs = _daemon_request(info, f"/api/session/{sid}/message?limit=10&order=desc")
        if st != 200 or not isinstance(msgs, dict):
            raise RuntimeError(f"Failed to fetch messages (HTTP {st}): {msgs}")

        assistant_msg = None
        for m in msgs.get("data", []):
            if m.get("type") == "assistant":
                assistant_msg = m
                break

        if not assistant_msg:
            raise RuntimeError("No assistant message returned by OpenCode")

        if assistant_msg.get("error"):
            err = assistant_msg["error"]
            err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            raise RuntimeError(f"OpenCode error: {err_msg}")

        # Extract text content
        content = assistant_msg.get("content", [])
        text_parts = []
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
                elif isinstance(part, str):
                    text_parts.append(part)
        elif isinstance(content, str):
            text_parts.append(content)

        reply_text = "\n".join(text_parts).strip()
        tokens = assistant_msg.get("tokens", {})
        return {
            "text": reply_text,
            "model": model_name,
            "usage": {
                "prompt_tokens": tokens.get("input", 0),
                "completion_tokens": tokens.get("output", 0),
                "total_tokens": tokens.get("input", 0) + tokens.get("output", 0),
            },
        }
    finally:
        # Cleanup session
        _daemon_request(info, f"/api/session/{sid}", "DELETE")


class BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        if self.path in ("/v1/models", "/models"):
            models_list = [
                {
                    "id": m,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "opencode",
                }
                for m in FREE_MODELS
            ]
            self._send_json(200, {"object": "list", "data": models_list})
        elif self.path in ("/health", "/api/health"):
            info = get_service_info(auto_spawn=False)
            self._send_json(200, {"ok": True, "daemon_pid": info.pid, "daemon_url": info.url})
        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if self.path in ("/v1/chat/completions", "/chat/completions"):
            length = int(self.headers.get("content-length", 0))
            raw = self.rfile.read(length)
            try:
                body = json.loads(raw)
            except Exception as e:
                self._send_json(400, {"error": f"Invalid JSON: {e}"})
                return

            model = body.get("model", "glm-5.3-flash-free")
            messages = body.get("messages", [])

            # Extract user prompt and system prompt
            system_parts = []
            user_parts = []
            for msg in messages:
                role = msg.get("role")
                cnt = msg.get("content", "")
                if role == "system":
                    system_parts.append(cnt)
                elif role in ("user", "human"):
                    user_parts.append(cnt)

            prompt = "\n\n".join(user_parts)
            system_prompt = "\n\n".join(system_parts)

            try:
                res = chat_generate(model, prompt, system_prompt=system_prompt)
            except Exception as e:
                self._send_json(500, {"error": str(e)})
                return

            # Format OpenAI response
            resp = {
                "id": f"chatcmpl-{secrets.token_hex(8)}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": res["text"]},
                        "finish_reason": "stop",
                    }
                ],
                "usage": res["usage"],
            }
            self._send_json(200, resp)
        else:
            self._send_json(404, {"error": "Not found"})

    def _send_json(self, status: int, data: dict) -> None:
        payload = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)


def run_server(port: int = DEFAULT_PORT) -> None:
    info = get_service_info()
    server = ThreadingHTTPServer(("127.0.0.1", port), BridgeHandler)
    print(f"[bridge] OpenCode OpenAI bridge listening on http://127.0.0.1:{port}")
    print(f"[bridge] Forwarding to local OpenCode daemon on {info.url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[bridge] Stopped.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    sb = sub.add_parser("serve", help="run OpenAI-compatible HTTP bridge server")
    sb.add_argument("--port", type=int, default=DEFAULT_PORT, help="port to bind (default: 8791)")

    sc = sub.add_parser("chat", help="send one-off prompt to OpenCode model")
    sc.add_argument("prompt", help="prompt text")
    sc.add_argument("--model", default="glm-5.3-flash-free", help="model name")
    sc.add_argument("--system", default="", help="optional system prompt")

    sm = sub.add_parser("models", help="list supported free models")

    a = p.parse_args()
    if a.cmd == "serve":
        run_server(a.port)
    elif a.cmd == "models":
        for m in FREE_MODELS:
            print(f"opencode/{m}")
    elif a.cmd == "chat":
        res = chat_generate(a.model, a.prompt, system_prompt=a.system)
        print(res["text"])


if __name__ == "__main__":
    main()
