#!/usr/bin/env python3
"""
grok2api_bridge — a MINIMAL, local, grok2api-compatible OpenAI endpoint that is
backed by Grok/xAI **SSO cookie tokens** (the ones grok-register produces).

WHY THIS EXISTS
---------------
9Router's built-in provider id "xai" is OAuth-only (it stores an accessToken /
refreshToken / idToken pair, scope "grok-cli:access api:access"). A grok.com
registration yields only an **SSO cookie** (`sso=<token>` / `sso-rw=<token>`),
which is NOT an OAuth token and therefore cannot be dropped into the built-in
"xai" provider.

The workable path (as discovered by scripts/grok_9router.py) is to register an
*openai-compatible* providerNode in 9Router whose baseUrl points at a "grok2api"
server that exposes POST /v1/chat/completions and GET /v1/models. This file is
that server — a thin forwarder from the OpenAI chat shape to xAI/Grok using the
SSO cookie.

    grok-register (token.json)  ──►  grok2api_bridge.py (:8787/v1)  ──►  9Router
                                                                        (openai-compatible node)

WHAT IT DOES
------------
  * Loads SSO tokens from (in priority order):
      --tokens FILE
      GROK2API_TOKENS env (path)
      grok-register/token.json  pools: ssoBasic / ssoSuper  (item -> {"token": ...})
      grok-register/accounts_*.txt  lines "email----password----sso"
  * GET  /v1/models            -> static OpenAI model list (see --upstream-model map)
  * GET  /healthz              -> simple liveness + token count
  * POST /v1/chat/completions  -> forwards to the xAI/Grok upstream using the SSO cookie
  * POST /v1/responses         -> same body, forwarded to the responses endpoint
  * Any request may carry `Authorization: Bearer <sso>`; if present it is used
    instead of the loaded pool (this is how grok_9router.py's per-token mode
    works — it sets apiKey = the SSO token).

UPSTREAM — GROUNDED (probed 2026-10-05)
---------------------------------------
Grok/xAI has TWO different auth surfaces and they are NOT interchangeable:

  * OAuth Bearer — `https://cli-chat-proxy.grok.com/v1`, an OpenAI-shaped /v1
    surface. This is what grok-register's CPA export mints (access_token /
    refresh_token, scope "grok-cli:access api:access"; see cpa_xai/schema.py).
    Probed live: an anonymous GET /v1/models answers 401
    ("Invalid or expired credentials ... no auth context"). It does NOT accept
    an SSO cookie.
  * SSO cookie — `POST https://grok.com/rest/app-chat/conversations/new`, the
    endpoint the grok.com web app itself calls, with the token in the
    `sso=<t>; sso-rw=<t>` cookies (exactly what registration_browser.py's
    enable_nsfw_for_token and sso_risk.inspect_sso_account_state set). Probed
    live: an anonymous POST answers
    `{"error":{"code":16,"message":"No credentials presented..."}}` and a dummy
    cookie answers `...Bad credentials...` — the route and the cookie auth are
    therefore correct; only a valid SSO is missing.

Because a grok-register SSO is a COOKIE (not a Bearer), the default upstream is
the grok.com app-chat route:

  --upstream-base   default https://grok.com
  --upstream-path   default /rest/app-chat/conversations/new
  --auth-mode       cookie (default) | bearer
  --cf-clearance    optional Cloudflare cf_clearance cookie value
  --raw             skip translation (send body verbatim, return upstream as-is)

With `--auth-mode cookie` the OpenAI request is translated to Grok's
`{"message","modelName"}` shape and the streamed reply is folded back into an
OpenAI `chat.completion`. `--raw` bypasses that so you can inspect the real wire
shape if grok.com changes.

For an OAuth Bearer credential instead, use the OAuth surface:
  --upstream-base https://cli-chat-proxy.grok.com --upstream-path /v1/chat/completions --auth-mode bearer

HONESTY: grok.com sits behind Cloudflare and expects a real browser TLS
fingerprint, so the bridge uses curl_cffi Chrome impersonation when available
(`--no-impersonate` forces plain httpx). The *request* shape and the auth
mechanism above are grounded and live-probed; the app-chat *response* body is
parsed best-effort (result.response.message / .token) and returns the raw
upstream body on any mismatch so nothing is hidden.

CLI
---
  python grok2api_bridge.py                       # 127.0.0.1:8787, auto-load pool
  python grok2api_bridge.py --port 8787
  python grok2api_bridge.py --tokens /path/to/token.json
  python grok2api_bridge.py --upstream-base https://grok.com --auth-mode cookie

Requires: fastapi, uvicorn, httpx  (all present in the managed venv
/home/amen/.local/share/auto-freecf/venv).

WIRING INTO 9ROUTER
-------------------
  # start the bridge
  /home/amen/.local/share/auto-freecf/venv/bin/python \
      /home/amen/Auto-FreeCF/scripts/grok2api_bridge.py --port 8787

  # point 9Router's grok2api node at it (writes a 9Router openai-compatible node)
  kancahub grok inject --base-url http://127.0.0.1:8787/v1
  # or, explicitly:
  export GROK2API_BASE=http://127.0.0.1:8787
  python3 /home/amen/Auto-FreeCF/scripts/grok_9router.py --base-url "$GROK2API_BASE" --dry-run

This file is ADDITIVE: it does not modify any existing script or the 9Router DB.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    import httpx
except Exception:  # pragma: no cover - import-time guard
    httpx = None  # type: ignore[assignment]

try:
    from curl_cffi.requests import AsyncSession as _CurlAsyncSession  # Chrome TLS impersonation
except Exception:  # pragma: no cover - import-time guard
    _CurlAsyncSession = None  # type: ignore[assignment]

try:
    from fastapi import FastAPI, Header, Request
    from fastapi.responses import JSONResponse
except Exception:  # pragma: no cover - import-time guard
    FastAPI = None  # type: ignore[assignment]
    Header = None  # type: ignore[assignment]
    Request = None  # type: ignore[assignment]
    JSONResponse = None  # type: ignore[assignment]


# --------------------------------------------------------------------------- const
GROK_REGISTER_DIR = Path(os.environ.get("GROK_REGISTER_DIR", Path.home() / "grok-register"))

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
# SSO-cookie auth talks to the grok.com web app's own chat route (see UPSTREAM above).
DEFAULT_UPSTREAM_BASE = "https://grok.com"
DEFAULT_UPSTREAM_CHAT_PATH = "/rest/app-chat/conversations/new"
DEFAULT_UPSTREAM_RESPONSES_PATH = "/rest/app-chat/conversations/new"
DEFAULT_UPSTREAM_MODEL = "grok-4"

# Models advertised by GET /v1/models (OpenAI shape). 9Router only needs the ids.
MODEL_IDS = ["grok-4", "grok-3", "grok-3-mini", "grok-2", "grok-beta"]


def _clean_sso(raw: str) -> str:
    """Strip an 'sso=' prefix and any trailing cookie attributes; return bare token."""
    t = (raw or "").strip()
    if t.lower().startswith("sso="):
        t = t[4:]
    return t.split(";")[0].strip()


def _messages_to_prompt(messages: Any) -> str:
    """Flatten an OpenAI messages array into one prompt string for grok.com."""
    if not isinstance(messages, list):
        return str(messages or "")
    parts: list[str] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "user")
        content = m.get("content")
        if isinstance(content, list):  # OpenAI multi-part content
            content = " ".join(
                str(c.get("text", "")) for c in content if isinstance(c, dict)
            )
        text = str(content or "").strip()
        if not text:
            continue
        parts.append(f"{role}: {text}" if role != "user" else text)
    return "\n\n".join(parts)


def _extract_reply(result: dict, raw: bool) -> tuple[str, str | None]:
    """Pull (text, model) out of grok.com's app-chat JSON, best-effort."""
    if not isinstance(result, dict):
        return str(result), None
    model = result.get("modelName") or result.get("model")
    node = result.get("result") if isinstance(result.get("result"), dict) else result
    for container in (node, result):
        if not isinstance(container, dict):
            continue
        resp = container.get("response")
        if isinstance(resp, dict):
            for key in ("message", "token", "text", "content"):
                if resp.get(key):
                    return str(resp[key]), (model if isinstance(model, str) else None)
        if isinstance(resp, str) and resp:
            return resp, (model if isinstance(model, str) else None)
        for key in ("message", "text", "content"):
            if isinstance(container.get(key), str) and container[key]:
                return container[key], (model if isinstance(model, str) else None)
    return "", (model if isinstance(model, str) else None)


def _to_openai(payload: dict, model: str) -> dict:
    """OpenAI chat.completion envelope from a grok.com app-chat response."""
    text, upstream_model = _extract_reply(payload, raw=False)
    now = int(time.time())
    return {
        "id": f"chatcmpl-grok2api-{now}",
        "object": "chat.completion",
        "created": now,
        "model": upstream_model or model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


# --------------------------------------------------------------------------- tokens
def load_tokens_from_json(path: Path) -> list[dict]:
    """grok-register token.json: {"ssoBasic": [{"token": .., "note": ..}], "ssoSuper": [...]}."""
    out: list[dict] = []
    try:
        data = json.loads(path.read_text())
    except Exception as exc:  # noqa: BLE001
        print(f"  ! skip {path}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return out
    pools = data if isinstance(data, dict) else {}
    for pool, items in pools.items():
        if not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, dict):
                tok = _clean_sso(it.get("token", ""))
                note = it.get("note")
            else:
                tok = _clean_sso(str(it))
                note = None
            if tok:
                out.append({"token": tok, "email": note or None, "pool": pool})
    return out


def load_tokens_from_accounts_txt(path: Path) -> list[dict]:
    """grok-register accounts_*.txt: 'email----password----sso' per line."""
    out: list[dict] = []
    try:
        text = path.read_text(errors="replace")
    except Exception as exc:  # noqa: BLE001
        print(f"  ! skip {path}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "----" not in line:
            continue
        parts = line.split("----")
        if len(parts) < 3:
            continue
        tok = _clean_sso(parts[-1])
        email = parts[0].strip()
        if tok:
            out.append({"token": tok, "email": email if "@" in email else None, "pool": path.name})
    return out


def load_tokens(tokens_arg: str | None) -> list[dict]:
    """Resolve the token source (CLI > env > grok-register defaults) and de-dupe."""
    candidates: list[Path] = []
    explicit = (tokens_arg or os.environ.get("GROK2API_TOKENS") or "").strip()
    if explicit:
        p = Path(explicit).expanduser()
        candidates.append(p)
    else:
        tj = GROK_REGISTER_DIR / "token.json"
        if tj.exists():
            candidates.append(tj)
        candidates += sorted(Path(p) for p in glob.glob(str(GROK_REGISTER_DIR / "accounts_*.txt")))

    out: list[dict] = []
    seen: set[str] = set()
    for f in candidates:
        if not f.exists():
            if explicit:
                print(f"  ! token source not found: {f}", file=sys.stderr)
            continue
        rows = load_tokens_from_json(f) if f.suffix == ".json" else load_tokens_from_accounts_txt(f)
        for r in rows:
            if r["token"] not in seen:
                seen.add(r["token"])
                out.append(r)
    return out


# --------------------------------------------------------------------------- app
def build_app(args: argparse.Namespace) -> "FastAPI":
    tokens = load_tokens(args.tokens)
    upstream_base = args.upstream_base.rstrip("/")
    upstream_chat_path = args.upstream_path
    upstream_responses_path = args.upstream_responses_path
    auth_mode = args.auth_mode
    default_model = args.upstream_model
    cf_clearance = str(getattr(args, "cf_clearance", "") or "").strip()
    use_impersonate = bool(getattr(args, "impersonate", True)) and _CurlAsyncSession is not None
    raw_mode = bool(getattr(args, "raw", False))

    app = FastAPI(title="grok2api_bridge", version="0.1.0")

    def pick_token(authorization: str | None) -> str | None:
        """Prefer an explicit Bearer token from the caller (per-token 9Router mode)."""
        if authorization and authorization.lower().startswith("bearer "):
            tok = _clean_sso(authorization[7:])
            if tok:
                return tok
        if tokens:
            return tokens[0]["token"]
        return None

    def auth_headers(sso: str) -> dict[str, str]:
        if auth_mode == "bearer":
            return {"Authorization": f"Bearer {sso}"}
        # default: cookie mode, mirroring registration_browser.py / sso_risk.py
        cookie = f"sso={sso}; sso-rw={sso}"
        if cf_clearance:
            cookie += f"; cf_clearance={cf_clearance}"
        return {"Cookie": cookie}

    async def forward(path: str, body: dict, sso: str) -> "JSONResponse":
        model = str(body.get("model") or default_model)
        if args.model_map:
            model = args.model_map.get(model, model)

        if raw_mode or auth_mode == "bearer":
            payload = dict(body)
            payload["model"] = model
        else:
            # Translate OpenAI -> grok.com app-chat shape.
            payload = {
                "message": _messages_to_prompt(body.get("messages")),
                "modelName": model,
                "fileAttachments": [],
                "imageAttachments": [],
                "disableSearch": False,
                "enableImageGeneration": True,
                "returnImageBytes": False,
                "returnRawGrokInXaiRequest": False,
                "enableImageStreaming": True,
                "imageGenerationCount": 2,
                "forceConcise": False,
                "toolOverrides": {},
                "enableSideBySide": True,
                "sendFinalMetadata": True,
                "isReasoning": False,
                "disableTextFollowUps": False,
                "responseMetadata": {"modelConfigOverride": {"modelMap": {}}},
            }

        url = upstream_base + path
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "grok2api_bridge/0.2",
            **auth_headers(sso),
        }
        if auth_mode == "cookie" and not raw_mode:
            headers["origin"] = "https://grok.com"
            headers["referer"] = "https://grok.com/"
        try:
            if use_impersonate:
                async with _CurlAsyncSession(impersonate="chrome", timeout=args.timeout) as client:
                    resp = await client.post(url, headers=headers, json=payload)
            else:
                if httpx is None:
                    return JSONResponse({"error": {"message": "httpx unavailable"}}, status_code=500)
                async with httpx.AsyncClient(timeout=args.timeout) as client:
                    resp = await client.post(url, headers=headers, json=payload)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                {"error": {"message": f"upstream request failed: {type(exc).__name__}: {exc}",
                           "type": "upstream_error", "upstream": url}},
                status_code=502,
            )

        status = resp.status_code
        ctype = resp.headers.get("content-type", "")
        text = resp.text
        parsed = None
        if "application/json" in ctype:
            try:
                parsed = resp.json()
            except Exception:  # noqa: BLE001
                parsed = None
            # grok.com returns application/json even for its {"error":...} envelope.
            if isinstance(parsed, dict) and isinstance(parsed.get("error"), dict):
                return JSONResponse(parsed, status_code=status if status >= 400 else 502)
            if status >= 400:
                return JSONResponse(
                    {"error": {"message": "upstream error", "upstream_status": status,
                               "upstream": url, "body": text[:2000]}},
                    status_code=status,
                )
            if raw_mode or auth_mode == "bearer":
                return JSONResponse(parsed if parsed is not None else {"raw": text[:4000]},
                                    status_code=status)
            return JSONResponse(_to_openai(parsed if isinstance(parsed, dict) else {}, model),
                                status_code=200)

        # Non-JSON (SSE text or an HTML error) — surface it so flags can be adjusted.
        return JSONResponse(
            {"error": {"message": "non-JSON upstream response",
                       "upstream_status": status, "upstream": url,
                       "body": text[:2000]}},
            status_code=status if status >= 400 else 502,
        )

    @app.get("/healthz")
    async def healthz() -> "JSONResponse":
        return JSONResponse({
            "ok": True,
            "tokens": len(tokens),
            "upstream_base": upstream_base,
            "upstream_chat_path": upstream_chat_path,
            "auth_mode": auth_mode,
            "impersonate": "chrome" if use_impersonate else "off",
            "raw": raw_mode,
            "time": int(time.time()),
        })

    @app.get("/v1/models")
    async def models() -> "JSONResponse":
        created = int(time.time())
        return JSONResponse({
            "object": "list",
            "data": [{"id": m, "object": "model", "created": created, "owned_by": "xai-grok2api"}
                     for m in MODEL_IDS],
        })

    @app.post("/v1/chat/completions")
    async def chat_completions(request: "Request",
                               authorization: str | None = Header(default=None)) -> "JSONResponse":
        sso = pick_token(authorization)
        if not sso:
            return JSONResponse(
                {"error": {"message": "no SSO token available (empty grok-register pool and no Bearer key)",
                           "type": "no_token"}},
                status_code=401,
            )
        try:
            body: Any = await request.json()
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": {"message": f"invalid JSON body: {exc}"}},
                                status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": {"message": "body must be a JSON object"}}, status_code=400)
        return await forward(upstream_chat_path, body, sso)

    @app.post("/v1/responses")
    async def responses(request: "Request",
                        authorization: str | None = Header(default=None)) -> "JSONResponse":
        sso = pick_token(authorization)
        if not sso:
            return JSONResponse(
                {"error": {"message": "no SSO token available", "type": "no_token"}},
                status_code=401,
            )
        try:
            body: Any = await request.json()
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"error": {"message": f"invalid JSON body: {exc}"}}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": {"message": "body must be a JSON object"}}, status_code=400)
        return await forward(upstream_responses_path, body, sso)

    # Stash for the CLI banner.
    app.state.token_count = len(tokens)
    return app


# --------------------------------------------------------------------------- main
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="grok2api_bridge.py",
        description="Minimal grok2api-compatible OpenAI endpoint backed by Grok SSO tokens.",
    )
    ap.add_argument("--host", default=DEFAULT_HOST, help=f"bind host (default: {DEFAULT_HOST})")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"bind port (default: {DEFAULT_PORT})")
    ap.add_argument("--tokens", default=None,
                    help="token.json / accounts_*.txt / path (default: env GROK2API_TOKENS, "
                         "else grok-register/token.json + accounts_*.txt)")
    ap.add_argument("--upstream-base", default=os.environ.get("GROK2API_UPSTREAM", DEFAULT_UPSTREAM_BASE),
                    help=f"xAI/Grok upstream base URL (default: {DEFAULT_UPSTREAM_BASE}; "
                         "also env GROK2API_UPSTREAM)")
    ap.add_argument("--upstream-path", default=DEFAULT_UPSTREAM_CHAT_PATH,
                    help=f"chat path appended to --upstream-base (default: {DEFAULT_UPSTREAM_CHAT_PATH})")
    ap.add_argument("--upstream-responses-path", default=DEFAULT_UPSTREAM_RESPONSES_PATH,
                    help=f"responses path appended to --upstream-base (default: {DEFAULT_UPSTREAM_RESPONSES_PATH})")
    ap.add_argument("--auth-mode", choices=["cookie", "bearer"], default="cookie",
                    help="how to present the SSO token upstream: Cookie sso=... (default) or Bearer")
    ap.add_argument("--cf-clearance", default=os.environ.get("GROK2API_CF_CLEARANCE", ""),
                    help="optional Cloudflare cf_clearance cookie value (also env GROK2API_CF_CLEARANCE)")
    ap.add_argument("--no-impersonate", dest="impersonate", action="store_false",
                    help="use plain httpx instead of curl_cffi Chrome TLS impersonation")
    ap.add_argument("--raw", action="store_true",
                    help="do not translate: send the request body verbatim and return the upstream body as-is")
    ap.set_defaults(impersonate=True, raw=False)
    ap.add_argument("--upstream-model", default=DEFAULT_UPSTREAM_MODEL,
                    help=f"model used when the request omits one (default: {DEFAULT_UPSTREAM_MODEL})")
    ap.add_argument("--model-map", default="",
                    help="comma list of clientModel=upstreamModel remaps, e.g. grok-4=grok-4-latest")
    ap.add_argument("--timeout", type=float, default=120.0, help="upstream timeout seconds (default: 120)")
    args = ap.parse_args(argv)
    model_map: dict[str, str] = {}
    for pair in str(args.model_map).split(","):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            model_map[k.strip()] = v.strip()
    args.model_map = model_map
    return args


def main(argv: list[str] | None = None) -> int:
    if FastAPI is None or httpx is None:
        print("✗ fastapi/httpx not importable. Run with the managed venv:\n"
              "  /home/amen/.local/share/auto-freecf/venv/bin/python " + __file__,
              file=sys.stderr)
        return 1
    args = parse_args(argv)
    app = build_app(args)

    import uvicorn  # imported here so --help works without uvicorn installed

    print(f"grok2api_bridge on http://{args.host}:{args.port}")
    print(f"  tokens loaded        : {app.state.token_count}")
    print(f"  upstream             : {args.upstream_base}{args.upstream_path}")
    print(f"  auth mode            : {args.auth_mode}")
    print(f"  TLS impersonation    : {'chrome' if args.impersonate and _CurlAsyncSession else 'off'}")
    print(f"  default model        : {args.upstream_model}")
    print(f"  wire into 9Router    : kancahub grok inject --base-url http://{args.host}:{args.port}/v1")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
