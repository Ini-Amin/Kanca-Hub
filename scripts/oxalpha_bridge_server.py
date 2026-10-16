#!/usr/bin/env python3
"""oxalpha_bridge_server.py — local OpenAI-compatible bridge to oxalpha.com chat.

oxalpha.com serves a free no-login chat (currently z-ai/glm-5.3-flash via
CoreWeave) behind a plain CSRF + session-cookie gate. No API key, no signup:

  GET  /chat       -> HTML with meta csrf-token + XSRF-TOKEN/ox_alpha_session cookies
  POST /api/chat   {model, messages} -> SSE chat.completion.chunk stream

Quota is per-IP: ~2 messages then HTTP 428 turnstile_required. So the bridge
routes upstream through the shared relay (:25346) and surfaces 428 honestly
as 429 (client backs off / user solves one Turnstile in a browser) instead of
hammering.

Usage:
  python3 scripts/oxalpha_bridge_server.py [--port 8788]

Endpoints:
  GET  /health
  GET  /v1/models
  POST /v1/chat/completions   (OpenAI shape in, SSE or JSON out)
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = "https://oxalpha.com"
MODEL = "z-ai/glm-5.3-flash"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# Same relay the zcode bridge uses (:25346 -> clean exit). Empty = direct.
UPSTREAM_PROXY = os.environ.get("OXALPHA_UPSTREAM_PROXY", "http://127.0.0.1:25346")


def _opener():
    handlers = []
    proxy = (UPSTREAM_PROXY or "").strip()
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    jar = http.cookiejar.CookieJar()
    handlers.append(urllib.request.HTTPCookieProcessor(jar))
    op = urllib.request.build_opener(*handlers)
    op.addheaders = [("user-agent", UA)]
    return op


def _session(op):
    """Fresh session: GET /chat for cookies + CSRF token."""
    html = op.open(BASE + "/chat", timeout=20).read().decode()
    m = re.search(r'meta[^>]*csrf-token[^>]*content="([^"]+)"', html)
    if not m:
        raise RuntimeError("csrf token not found")
    return m.group(1)


def call_upstream(messages, timeout=120):
    """POST /api/chat, return (response, None) or (None, error_dict)."""
    op = _opener()
    try:
        csrf = _session(op)
    except Exception as e:  # noqa: BLE001
        return None, {"http": 502, "error": f"session failed: {e}"}
    body = json.dumps({"model": MODEL, "messages": messages}).encode()
    req = urllib.request.Request(
        BASE + "/api/chat", data=body, method="POST",
        headers={"Content-Type": "application/json", "X-CSRF-TOKEN": csrf,
                 "Referer": BASE + "/chat", "Origin": BASE,
                 "Accept": "text/event-stream"})
    try:
        return op.open(req, timeout=timeout), None
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode())
        except Exception:  # noqa: BLE001
            err = {"error": f"HTTP {e.code}"}
        return None, {"http": e.code, "body": err}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[oxalpha] %s\n" % (fmt % args))

    def _send(self, status, body):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path in ("/", "/health"):
            return self._send(200, {"ok": True, "upstream": "oxalpha.com",
                                    "model": MODEL,
                                    "egress": UPSTREAM_PROXY or "direct"})
        if path.endswith("/models"):
            return self._send(200, {"object": "list", "data": [
                {"id": MODEL, "object": "model", "owned_by": "oxalpha"}]})
        self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._send(404, {"error": {"message": "not found"}})
        try:
            n = int(self.headers.get("content-length") or 0)
        except (TypeError, ValueError):
            n = 0
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except Exception:  # noqa: BLE001
            return self._send(400, {"error": {"message": "invalid JSON"}})
        msgs = [{"role": m.get("role", "user"), "content": m.get("content", "")}
                for m in req.get("messages", [])
                if isinstance(m.get("content"), str)]
        if not msgs:
            return self._send(400, {"error": {"message": "no messages"}})
        stream = bool(req.get("stream", True))
        resp, err = call_upstream(msgs)
        if err is not None:
            body = err.get("body") or {}
            # 428 turnstile checkpoint -> 429 so clients back off honestly
            if err.get("http") == 428 or body.get("code") == "turnstile_required":
                return self._send(429, {"error": {
                    "type": "rate_limit",
                    "message": "oxalpha Turnstile checkpoint (~2 msgs/IP): "
                               "solve one check in a browser, then retry"}})
            return self._send(err.get("http", 502),
                              {"error": {"message": str(body.get("error") or err)}})
        if stream:
            return self._stream(resp, req)
        text = self._collect(resp)
        self._send(200, {"id": "chatcmpl-" + uuid.uuid4().hex[:24],
                         "object": "chat.completion", "created": int(time.time()),
                         "model": req.get("model") or MODEL,
                         "choices": [{"index": 0,
                                      "message": {"role": "assistant", "content": text},
                                      "finish_reason": "stop"}],
                         "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                                   "total_tokens": 0}})

    def _collect(self, resp):
        text = ""
        while True:
            line = resp.readline()
            if not line:
                break
            line = line.decode(errors="replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                for ch in json.loads(payload).get("choices", []):
                    text += (ch.get("delta") or {}).get("content") or ""
            except Exception:  # noqa: BLE001
                pass
        return text

    def _stream(self, resp, req):
        self.send_response(200)
        self.send_header("content-type", "text/event-stream; charset=utf-8")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "close")
        self.end_headers()
        cid = "chatcmpl-" + uuid.uuid4().hex[:24]
        created = int(time.time())
        model = req.get("model") or MODEL

        def chunk(delta, fr=None):
            c = {"id": cid, "object": "chat.completion.chunk", "created": created,
                 "model": model,
                 "choices": [{"index": 0, "delta": delta, "finish_reason": fr}]}
            self.wfile.write(b"data: " + json.dumps(c).encode() + b"\n\n")
            self.wfile.flush()

        chunk({"role": "assistant", "content": ""})
        buf = b""
        try:
            while True:
                data = resp.read(4096)
                if not data:
                    break
                buf += data
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.decode(errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        ev = json.loads(payload)
                    except Exception:  # noqa: BLE001
                        continue
                    for ch in ev.get("choices", []):
                        t = ((ch.get("delta") or {}).get("content")) or ""
                        if t:
                            chunk({"content": t})
        finally:
            chunk({}, fr="stop")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            try:
                resp.close()
            except Exception:  # noqa: BLE001
                pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="oxalpha.com chat bridge (OpenAI-compatible)")
    ap.add_argument("--port", type=int, default=8788)
    ap.add_argument("--bind", default="127.0.0.1")
    a = ap.parse_args(argv)
    srv = ThreadingHTTPServer((a.bind, a.port), Handler)
    print(f"[oxalpha] bridge on {a.bind}:{a.port} -> {BASE} via {UPSTREAM_PROXY or 'direct'}",
          flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
