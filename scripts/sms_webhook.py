#!/usr/bin/env python3
"""
scripts/sms_webhook.py — receive Litensi SMS callbacks and expose the code.

Litensi's phone Activation has no polling API; instead you configure a
"Callback SMS" URL on https://litensi.id/profile/api, and Litensi POSTs JSON to
it like:
    {"activationId":123456,"service":"go","text":"Your Google verification code
     is 12345","code":"12345","country":6,"receivedAt":"2026-06-05T12:28:14Z"}

This runs a tiny HTTP listener that:
  * writes every received code to ~/.config/auto-freecf/sms_codes.jsonl
  * keeps the newest code in ~/.config/auto-freecf/sms_latest.json
  * returns HTTP 200 (Litensi requires it)

Expose it publicly with a tunnel, e.g.:
  cloudflared tunnel --url http://127.0.0.1:8799
then paste https://<random>.trycloudflare.com/sms into Litensi's URL webhook box.

Usage:
  python scripts/sms_webhook.py                 # listen on 127.0.0.1:8799
  python scripts/sms_webhook.py --port 8799
  python scripts/sms_webhook.py --latest         # print latest code + exit
  python scripts/sms_webhook.py --wait 180       # block until a code arrives, print it
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CFG = Path.home() / ".config" / "auto-freecf"
LOG = CFG / "sms_codes.jsonl"
LATEST = CFG / "sms_latest.json"
CODE_RE = re.compile(r"\b(\d{4,8})\b")


def _store(payload: dict) -> str | None:
    """Extract + persist the code. Returns the code or None."""
    text = " ".join(str(payload.get(k) or "") for k in ("code", "text", "message", "sms"))
    code = str(payload.get("code") or "").strip()
    if not code:
        m = CODE_RE.search(text)
        code = m.group(1) if m else ""
    rec = {"ts": int(time.time() * 1000), "code": code, **payload}
    CFG.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    if code:
        LATEST.write_text(json.dumps({"code": code, "ts": rec["ts"], "raw": payload}))
    return code or None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _respond(self, code: int, body: bytes = b"ok"):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("latest") and LATEST.exists():
            self._respond(200, LATEST.read_bytes()); return
        self._respond(200, b"sms webhook up")

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n).decode("utf-8", "ignore") if n else ""
            try:
                payload = json.loads(raw)
            except Exception:
                payload = {"text": raw}
            if isinstance(payload, list):
                payload = payload[0] if payload else {}
            code = _store(payload)
            print(f"  [sms] received: code={code!r} raw={raw[:160]}")
        except Exception as e:  # noqa: BLE001
            print(f"  [sms] error: {e}")
        self._respond(200, b"ok")  # Litensi requires HTTP 200


def serve(port: int, host: str = "127.0.0.1") -> None:
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"  sms webhook listening on http://{host}:{port}  (POST /  |  GET /latest)")
    print(f"  tunnel it:  cloudflared tunnel --url http://{host}:{port}")
    print(f"  paste  https://<...>.trycloudflare.com/sms  into Litensi → Callback SMS")
    srv.serve_forever()


def wait_code(timeout: float = 180) -> str | None:
    """Block until a NEW code appears in the latest file."""
    start = time.time()
    seen = LATEST.stat().st_mtime if LATEST.exists() else 0
    while time.time() - start < timeout:
        if LATEST.exists() and LATEST.stat().st_mtime > seen:
            try:
                c = json.loads(LATEST.read_text()).get("code")
                if c:
                    return c
            except Exception:
                pass
        time.sleep(2)
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Litensi SMS webhook listener")
    ap.add_argument("--port", type=int, default=8799)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--wait", type=float, default=0)
    a = ap.parse_args(argv)
    if a.latest:
        print(LATEST.read_text() if LATEST.exists() else "  (no code yet)")
        return 0
    if a.wait:
        c = wait_code(a.wait)
        print(f"  code: {c}")
        return 0 if c else 1
    try:
        serve(a.port, a.host)
    except KeyboardInterrupt:
        print("\n  stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())