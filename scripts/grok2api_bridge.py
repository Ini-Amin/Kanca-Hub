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

UPSTREAM — READ THIS (honesty section)
--------------------------------------
The exact *wire shape* of the xAI/Grok "chat" upstream when authenticated ONLY
by an SSO cookie is not fully pinned down in the sources we have:

  * grok-register sets `cookie: sso=<t>; sso-rw=<t>` and then talks to
    google-Play-Spanner-esque **gRPC-web** endpoints on grok.com / accounts.x.ai
    for account settings (registration_browser.py: set_tos_accepted,
    update_nsfw_settings). Those are account-management RPCs, not chat.
  * The OAuth-minted CPA credentials use `base_url = https://cli-chat-proxy.grok.com/v1`
    (cpa_xai/schema.py) — an OpenAI-shaped /v1 surface — but that credential is a
    Bearer access token, not an SSO cookie.
  * We did not find in-repo evidence of the cookie-authenticated /v1/chat/completions
    request body or the exact host used by real grok2api deployments.

So this bridge makes a *reasonable* choice and makes it trivial to change:

  --upstream-base   default https://cli-chat-proxy.grok.com
  --upstream-path   default /v1/chat/completions   (append to base)
  --auth-mode       cookie (default) | bearer
  --upstream-model  optional model-name remap, e.g. grok-4=grok-4-latest

With `--auth-mode cookie` (default) the SSO token is sent as
`Cookie: sso=<t>; sso-rw=<t>`; with `--auth-mode bearer` it is sent as
`Authorization: Bearer <t>`. Flip whichever your upstream actually wants. If the
upstream replies 4xx, the bridge returns the upstream status and a truncated body
so you can see exactly what it expects and adjust the flags — no guesswork hidden.

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
DEFAULT_UPSTREAM_BASE = "https://cli-chat-proxy.grok.com"
DEFAULT_UPSTREAM_MODEL = "grok-4"

# Models advertised by GET /v1/models (OpenAI shape). 9Router only needs the ids.
MODEL_IDS = ["grok-4", "grok-3", "grok-3-mini", "grok-2", "grok-beta"]


def _clean_sso(raw: str) -> str:
    """Strip an 'sso=' prefix and any trailing cookie attributes; return bare token."""
    t = (raw or "").strip()
    if t.lower().startswith("sso="):
        t = t[4:]
    return t.split(";")[0].strip()


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
        # default: cookie mode, mirroring registration_browser.py
        return {"Cookie": f"sso={sso}; sso-rw={sso}"}

    async def forward(path: str, body: dict, sso: str) -> "JSONResponse":
        if httpx is None:
            return JSONResponse({"error": {"message": "httpx unavailable"}}, status_code=500)
        model = str(body.get("model") or default_model)
        if args.model_map:
            model = args.model_map.get(model, model)
        payload = dict(body)
        payload["model"] = model
        url = upstream_base + path
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "grok2api_bridge/0.1",
            **auth_headers(sso),
        }
        try:
            async with httpx.AsyncClient(timeout=args.timeout) as client:
                resp = await client.post(url, headers=headers, json=payload)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse(
                {"error": {"message": f"upstream request failed: {type(exc).__name__}: {exc}",
                           "type": "upstream_error", "upstream": url}},
                status_code=502,
            )
        ctype = resp.headers.get("content-type", "")
        if "application/json" in ctype:
            try:
                return JSONResponse(resp.json(), status_code=resp.status_code)
            except Exception:  # noqa: BLE001
                pass
        # Non-JSON (e.g. SSE text or an HTML error) — surface it so the caller can adjust flags.
        return JSONResponse(
            {"error": {"message": "non-JSON upstream response",
                       "upstream_status": resp.status_code, "upstream": url,
                       "body": resp.text[:2000]}},
            status_code=resp.status_code if resp.status_code >= 400 else 502,
        )

    @app.get("/healthz")
    async def healthz() -> "JSONResponse":
        return JSONResponse({
            "ok": True,
            "tokens": len(tokens),
            "upstream_base": upstream_base,
            "upstream_chat_path": upstream_chat_path,
            "auth_mode": auth_mode,
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
    ap.add_argument("--upstream-path", default="/v1/chat/completions",
                    help="chat path appended to --upstream-base (default: /v1/chat/completions)")
    ap.add_argument("--upstream-responses-path", default="/v1/responses",
                    help="responses path appended to --upstream-base (default: /v1/responses)")
    ap.add_argument("--auth-mode", choices=["cookie", "bearer"], default="cookie",
                    help="how to present the SSO token upstream: Cookie sso=... (default) or Bearer")
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
    print(f"  default model        : {args.upstream_model}")
    print(f"  wire into 9Router    : kancahub grok inject --base-url http://{args.host}:{args.port}/v1")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
