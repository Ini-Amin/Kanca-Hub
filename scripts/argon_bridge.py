#!/usr/bin/env python3
"""Direct Gemini 4 Argon bridge (lithium7b account only).

WHY this exists: 9Router 0.5.104 maps ag/gemini-4-argon-* to upstream
`...(tier)` (parens) which Google 404s; the IDE's bare `gemini-4-argon-{tier}`
works but only on the IDE channel. This bridge replicates the IDE's EXACT
request shape (captured via MITM 2026-10-16, see argon_cap notes in AGENTS.md)
against daily-cloudcode-pa, so opencode/omp can call argon.

  Endpoint: POST https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse
  Body: {"project":"aicode-consumers","requestId":"checkpoint/<uuid>",
         "request":{"contents":[...]}, "model":"gemini-4-argon-{low,medium,high}",
         "userAgent":"antigravity","requestType":"checkpoint"}
  Token: read per-call from 9Router DB (its refresher keeps it fresh).
         Single account: lithium7b@gmail.com. Sequential use only.

Run:  python3 scripts/argon_bridge.py  (listens 127.0.0.1:8788)
Test: curl -X POST 127.0.0.1:8788/v1/chat/completions -d '{"messages":[{"role":"user","content":"hi"}]}'
"""
import json
import os
import sqlite3
import threading
import urllib.request
import urllib.error
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8788
DB = os.path.expanduser("~/.9router/db/data.sqlite")
EMAIL = "lithium7b@gmail.com"
ENDPOINT = "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse"
UA = "antigravity/hub/2.19.1 (aidev_client; os_type=linux; arch=amd64; cl=991101027)"
TIERS = {"low": "gemini-4-argon-low", "medium": "gemini-4-argon-medium",
         "high": "gemini-4-argon-high"}
_lock = threading.Lock()


def token():
    c = sqlite3.connect(DB)
    row = c.execute("SELECT data FROM providerConnections WHERE email=?", (EMAIL,)).fetchone()
    if not row:
        raise RuntimeError("no connection for " + EMAIL)
    return json.loads(row[0]).get("accessToken", "")


def upstream(messages, tier):
    model = TIERS.get((tier or "medium").lower(), TIERS["medium"])
    contents = [{"role": ("model" if m.get("role") == "assistant" else "user"),
                 "parts": [{"text": str(m.get("content") or "")}]}
                for m in messages if isinstance(m, dict)]
    body = {"project": "aicode-consumers", "requestId": "checkpoint/" + str(uuid.uuid4()),
            "request": {"contents": contents}, "model": model,
            "userAgent": "antigravity", "requestType": "checkpoint"}
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + token(),
                                          "Content-Type": "application/json",
                                          "User-Agent": UA})
    with _lock:
        raw = urllib.request.urlopen(req, timeout=300).read().decode("utf-8", "replace")
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data: ") and line != "data: [DONE]":
            try:
                d = json.loads(line[6:])
                for cand in ((d.get("response") or {}).get("candidates") or []):
                    for p in (((cand.get("content") or {}).get("parts")) or []):
                        if isinstance(p, dict) and "text" in p and "thought" not in p:
                            out.append(p["text"])
            except Exception:
                pass
    return "".join(out), model


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj, code=200):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path == "/health":
            return self._send({"ok": True, "model": "argon", "account": EMAIL})
        if self.path == "/v1/models" or self.path.endswith("/models"):
            return self._send({"object": "list", "data": [
                {"id": "argon-low", "object": "model", "owned_by": "argon"},
                {"id": "argon-medium", "object": "model", "owned_by": "argon"},
                {"id": "argon-high", "object": "model", "owned_by": "argon"}]})
        return self._send({"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            return self._send({"error": "not found"}, 404)
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode() or "{}")
        except Exception:
            return self._send({"error": "bad json"}, 400)
        tier = body.get("tier", "medium")
        if isinstance(body.get("model"), str) and body["model"].startswith("argon-"):
            tier = body["model"].split("-", 1)[1]
        try:
            text, model = upstream(body.get("messages") or [], tier)
        except urllib.error.HTTPError as e:
            return self._send({"error": "upstream HTTP %s" % e.code}, 502)
        except Exception as e:
            return self._send({"error": str(e)[:120]}, 500)
        self._send({"id": "argon-" + uuid.uuid4().hex[:8], "object": "chat.completion",
                    "model": model, "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}]})


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()
