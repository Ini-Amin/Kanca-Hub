#!/usr/bin/env python3
"""scripts/gemini_bridge.py — OpenAI-compatible bridge to https://gemini.google.com/app.

Uses the signed-in WEB session (cookies), never the Antigravity OAuth token: the
Cloud Code token cannot read the consumer Gemini web app. No browser runs per
request — gemini_webapi drives the web app's own batchexecute RPC over curl_cffi.

    python3 scripts/gemini_bridge.py login --cookies "__Secure-1PSID=..;__Secure-1PSIDTS=.."
    python3 scripts/gemini_bridge.py login --from-browser     # needs browser-cookie3
    python3 scripts/gemini_bridge.py serve [--port 8789] [--min-interval 3]
    python3 scripts/gemini_bridge.py ask "hi"                 # one-off smoke test

State: ~/.gemini-bridge/session.json (chmod 600) + rotated-cookie cache.
Pacing: one upstream generation at a time, >= --min-interval seconds apart, so a
burst from 9Router cannot hammer the consumer account.
Ponytail: chat is stateless (client resends history) and usage is estimated
len/4. Add ChatSession continuity / real counters when a caller needs them.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
import sys
import time
from pathlib import Path

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse

HOME = Path(os.environ.get("GEMINI_BRIDGE_HOME", Path.home() / ".gemini-bridge"))
SESSION = HOME / "session.json"
DEFAULT_PORT = 8789
DEFAULT_MIN_INTERVAL = 3.0
PSID, PSIDTS = "__Secure-1PSID", "__Secure-1PSIDTS"


# ───────────────────────────────────────────────────────────────── session
def save_session(cookies: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    SESSION.write_text(json.dumps(cookies, indent=1))
    SESSION.chmod(stat.S_IRUSR | stat.S_IWUSR)
    print(f"saved {list(cookies)} -> {SESSION}")


def load_session() -> dict:
    if not SESSION.exists():
        raise RuntimeError(f"no session at {SESSION} — run `login` first")
    return json.loads(SESSION.read_text())


def parse_cookie_string(raw: str) -> dict:
    out = {}
    for part in raw.replace("\n", ";").split(";"):
        for name in (PSID, PSIDTS):
            if part.strip().startswith(name + "="):
                out[name] = part.split("=", 1)[1].strip()
    return out


def cookies_from_browser() -> dict:
    import browser_cookie3 as bc3  # optional extra
    jar = bc3.load(domain_name="google.com")
    return {c.name: c.value for c in jar if c.name in (PSID, PSIDTS)}


# ───────────────────────────────────────────────────────────────── upstream
class Bridge:
    """Single serialized Gemini web client, minimum spacing between calls."""

    def __init__(self, min_interval: float) -> None:
        self.min_interval = min_interval
        self._lock = asyncio.Lock()
        self._last = 0.0
        self._client = None

    async def client(self):
        if self._client is None:
            from gemini_webapi import GeminiClient

            s = load_session()
            if PSID not in s:
                raise RuntimeError(f"{PSID} missing from {SESSION}")
            os.environ.setdefault("GEMINI_COOKIE_PATH", str(HOME))
            c = GeminiClient(s[PSID], s.get(PSIDTS), proxy=os.environ.get("GEMINI_PROXY") or None)
            await c.init(auto_refresh=True, verbose=False)
            self._client = c
        return self._client

    async def _slot(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last = time.monotonic()

    async def models(self) -> list[str]:
        c = await self.client()
        listed = c.list_models() or []
        return [m.model_name for m in listed] or ["gemini-3.5-flash"]

    async def generate(self, prompt: str, model: str | None):
        async with self._lock:
            await self._slot()
            c = await self.client()
            return await c.generate_content(prompt, model=model)

    async def stream(self, prompt: str, model: str | None):
        async with self._lock:
            await self._slot()
            c = await self.client()
            async for chunk in c.generate_content_stream(prompt, model=model):
                yield chunk


def flatten(messages: list[dict]) -> str:
    """OpenAI messages -> one prompt. Consecutive non-user turns are prefixed."""
    parts = []
    for m in messages or []:
        role = (m.get("role") or "user").lower()
        content = m.get("content") or ""
        if isinstance(content, list):  # parts array
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if not content:
            continue
        parts.append(content if role == "user" else f"{role.capitalize()}: {content}")
    return "\n\n".join(parts)


PLACEHOLDER = "gemini-web"


def pick_model(name: str | None) -> str | None:
    """Pass real gemini ids through; let the account default handle 9Router aliases."""
    n = (name or "").strip().split("/")[-1].lower()
    return n if n.startswith("gemini") and n != PLACEHOLDER else None


def usage(prompt: str, answer: str) -> dict:
    p, c = len(prompt) // 4, len(answer) // 4
    return {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p + c}


# ───────────────────────────────────────────────────────────────── server
KEYS_FILE = HOME / "keys.json"


def ensure_key() -> str:
    """Create a random local key on first serve (9Router uses it as the node's
    API key). Nothing else can call a loopback bridge, but a key keeps the
    OpenAI-compatible contract and the injector's auth path honest."""
    import secrets

    if KEYS_FILE.exists():
        try:
            return next(iter(json.loads(KEYS_FILE.read_text()).values()))
        except Exception:  # noqa: BLE001
            pass
    key = "sk-gw-" + secrets.token_hex(16)
    HOME.mkdir(parents=True, exist_ok=True)
    KEYS_FILE.write_text(json.dumps({"default": key}, indent=1))
    KEYS_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)
    print(f"created {KEYS_FILE} (key sk-gw-…); point 9Router's GeminiWeb node at it")
    return key


def check_auth(authorization: str | None, key: str) -> bool:
    if not authorization:
        return True  # loopback + some clients send no header
    return authorization.removeprefix("Bearer ").strip() in (key, "")


def make_app(bridge: Bridge, api_key: str):
    app = FastAPI(title="gemini-web-bridge")

    @app.get("/health")
    async def health():
        return {"ok": SESSION.exists(), "upstream": "gemini.google.com/app",
                "session": str(SESSION), "min_interval": bridge.min_interval,
                "proxy": os.environ.get("GEMINI_PROXY") or "direct"}

    @app.get("/v1/models")
    async def models():
        try:
            ids = await bridge.models()
        except Exception as e:  # noqa: BLE001
            return JSONResponse({"error": {"message": f"{type(e).__name__}: {e}"}}, status_code=502)
        now = int(time.time())
        return {"object": "list",
                "data": [{"id": i, "object": "model", "created": now, "owned_by": "google"} for i in ids]}

    @app.post("/v1/chat/completions")
    async def chat(req: Request, authorization: str | None = Header(default=None)):
        if not check_auth(authorization, api_key):
            return JSONResponse({"error": {"message": "invalid API key"}}, status_code=401)
        body = await req.json()
        prompt = flatten(body.get("messages"))
        if not prompt:
            return JSONResponse({"error": {"message": "empty messages"}}, status_code=400)
        model = pick_model(body.get("model"))
        mid = body.get("model") or PLACEHOLDER
        cid = "chatcmpl-gw-%d" % int(time.time() * 1000)
        if not body.get("stream"):
            try:
                out = await bridge.generate(prompt, model)
            except Exception as e:  # noqa: BLE001
                return JSONResponse({"error": {"message": f"{type(e).__name__}: {e}"}}, status_code=502)
            return {"id": cid, "object": "chat.completion", "created": int(time.time()),
                    "model": mid,
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": out.text}}],
                    "usage": usage(prompt, out.text or "")}

        async def sse():
            def frame(delta, finish=None):
                return "data: " + json.dumps({
                    "id": cid, "object": "chat.completion.chunk", "created": int(time.time()),
                    "model": mid, "choices": [{"index": 0, "finish_reason": finish,
                                               "delta": delta}]}) + "\n\n"
            yield frame({"role": "assistant", "content": ""})
            try:
                async for chunk in bridge.stream(prompt, model):
                    if chunk.text_delta:
                        yield frame({"content": chunk.text_delta})
            except Exception as e:  # noqa: BLE001
                yield "data: " + json.dumps({"error": {"message": f"{type(e).__name__}: {e}"}}) + "\n\n"
            yield frame({}, finish="stop")
            yield "data: [DONE]\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream")

    return app


# ───────────────────────────────────────────────────────────────── cli
def cmd_login(args) -> int:
    if args.cookies:
        got = parse_cookie_string(args.cookies)
    else:
        got = cookies_from_browser()
    if PSID not in got:
        print("no __Secure-1PSID found. Pass --cookies or sign in with a browser first.", file=sys.stderr)
        return 1
    save_session(got)
    print("Note: __Secure-1PSIDTS is rotated by the library; keep this dir writable.")
    return 0


def cmd_ask(args) -> int:
    bridge = Bridge(args.min_interval)
    out = asyncio.run(bridge.generate(args.prompt, pick_model(args.model)))
    print(out.text)
    return 0


def cmd_serve(args) -> int:
    import uvicorn

    if not SESSION.exists():
        print(f"warning: {SESSION} missing; /health will report ok=false", file=sys.stderr)
    key = ensure_key()
    uvicorn.run(make_app(Bridge(args.min_interval), key), host=args.host, port=args.port,
                log_level="info")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Gemini web bridge (OpenAI-compatible)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, fn in (("login", cmd_login), ("ask", cmd_ask), ("serve", cmd_serve)):
        p = sub.add_parser(name)
        p.add_argument("--min-interval", type=float, default=DEFAULT_MIN_INTERVAL,
                       help="minimum seconds between upstream generations (default 3)")
        if name == "login":
            p.add_argument("--cookies", help='cookie string containing __Secure-1PSID[TS]')
            p.add_argument("--from-browser", action="store_true", help="read them from a local browser")
        if name == "ask":
            p.add_argument("prompt")
            p.add_argument("--model")
        if name == "serve":
            p.add_argument("--host", default="127.0.0.1")
            p.add_argument("--port", type=int, default=DEFAULT_PORT)
        p.set_defaults(fn=fn)

    args = ap.parse_args(argv)
    if args.cmd == "login" and not (args.cookies or args.from_browser):
        args.from_browser = True
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())