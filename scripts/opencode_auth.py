#!/usr/bin/env python3
"""OpenCode Console device-flow auth: register/login -> device approve -> tokens.

Reverse-engineered from opencode v2.0.32 (mod_055_660979.js) and the console
bundle (index-Dirn5Dqt.js), verified live 2026-10-22:

  client_id      "opencode-cli"  (Jg in the CLI, hardcoded)
  code endpoint  POST {console}/auth/device/code   {client_id, supports_org_scope}
  approve        POST {console}/auth/device/approve {user_code[, org_id]}  (session cookie)
  poll token     POST {console}/auth/device/token   {grant_type: device_code, device_code, client_id}
  register       POST {console}/auth/register  {email, password, challengeToken?}  -> 201
  login          POST {console}/auth/login     {email, password, challengeToken?}  -> session cookie

Console = https://opencode.ai/console (also console.opencode.ai). The CLI
stores the result in ~/.local/share/opencode/auth.json under provider
"opencode": {type:"oauth", access, refresh, expires}.

BLOCKER (measured): register/login answer 400/401 "Complete the security
check" without a challengeToken from an hCaptcha solve (sitekey
224e469e-a0ba-4c40-b5aa-a2fa838a6bd8, provider switchable to turnstile
server-side). Token refresh uses POST /auth/refresh with the session cookie,
no captcha.

Never hammer: z.ai-style anti-abuse lives on this host too (POSTs from one
IP start getting connection resets after a handful of requests).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

CONSOLE = "https://opencode.ai/console"
CLIENT_ID = "opencode-cli"
AUTH_JSON = Path.home() / ".local/share/opencode/auth.json"
UA = "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OpenCode-User/1.0; +https://opencode.ai"


def _req(url: str, payload: dict | None = None, *, cookie: str = "", method: str = "") -> tuple[int, dict, str]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
    req.add_header("content-type", "application/json")
    req.add_header("user-agent", UA)
    req.add_header("origin", "https://opencode.ai")
    if cookie:
        req.add_header("cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read().decode()
            return r.status, dict(r.headers), body
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode()


def _json(body: str) -> dict:
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body}


def start_device() -> dict:
    st, _, body = _req(f"{CONSOLE}/auth/device/code",
                       {"client_id": CLIENT_ID, "supports_org_scope": True})
    if st != 200:
        sys.exit(f"device/code HTTP {st}: {body[:200]}")
    d = _json(body)
    url = d["verification_uri_complete"]
    print(f"user_code : {d['user_code']}")
    print(f"verify    : https://opencode.ai{url if url.startswith('/') else '/' + url}")
    print(f"expires   : {d['expires_in']}s, poll every {d['interval']}s")
    return d


def poll_token(device_code: str, interval: int, expires_in: int) -> dict | None:
    deadline = time.time() + expires_in
    while time.time() < deadline:
        st, _, body = _req(f"{CONSOLE}/auth/device/token", {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
            "client_id": CLIENT_ID,
        })
        d = _json(body)
        if st == 200 and "access_token" in d:
            return d
        err = d.get("error", body[:120])
        if err == "authorization_pending":
            time.sleep(interval)
        elif err == "slow_down":
            interval += 5
            time.sleep(interval)
        else:
            sys.exit(f"device/token failed: {err}")
    return None


def register(email: str, password: str, challenge: str = "") -> None:
    payload = {"email": email, "password": password}
    if challenge:
        payload["challengeToken"] = challenge
    st, hdr, body = _req(f"{CONSOLE}/auth/register", payload)
    print(f"HTTP {st}: {body[:300]}")
    if st == 201:
        _save_session_cookie(hdr)


def login(email: str, password: str, challenge: str = "") -> str:
    payload = {"email": email, "password": password}
    if challenge:
        payload["challengeToken"] = challenge
    st, hdr, body = _req(f"{CONSOLE}/auth/login", payload)
    print(f"HTTP {st}: {body[:300]}")
    cookie = _session_cookie(hdr)
    if st == 200 and cookie:
        (Path.home() / ".config/auto-freecf").mkdir(parents=True, exist_ok=True)
        (Path.home() / ".config/auto-freecf/opencode_session.txt").write_text(cookie)
        print("session cookie saved to ~/.config/auto-freecf/opencode_session.txt")
        return cookie
    return ""


def approve(user_code: str, cookie: str) -> None:
    st, _, body = _req(f"{CONSOLE}/auth/device/approve", {"user_code": user_code}, cookie=cookie)
    print(f"HTTP {st}: {body[:300]}")


def _session_cookie(hdr: dict) -> str:
    out = []
    for k, v in hdr.items():
        if k.lower() == "set-cookie" and "session" in v.lower():
            out.append(v.split(";", 1)[0])
    return "; ".join(out)


def _save_session_cookie(hdr: dict) -> None:
    c = _session_cookie(hdr)
    if c:
        print(f"session cookie: {c[:40]}…")


def save_auth(token: dict) -> None:
    AUTH_JSON.parent.mkdir(parents=True, exist_ok=True)
    auth = json.loads(AUTH_JSON.read_text()) if AUTH_JSON.exists() else {}
    auth["opencode"] = {
        "type": "oauth",
        "access": token.get("access_token", ""),
        "refresh": token.get("refresh_token", ""),
        "expires": int(time.time()) + int(token.get("expires_in", 3600)),
    }
    AUTH_JSON.write_text(json.dumps(auth, indent=2))
    print(f"auth.json updated: {AUTH_JSON}")


def cmd_flow(args: argparse.Namespace) -> None:
    """Full manual flow: device code -> (you approve in browser) -> tokens saved."""
    d = start_device()
    tok = poll_token(d["device_code"], d["interval"], d["expires_in"])
    if not tok:
        sys.exit("device flow expired without approval")
    print(json.dumps({k: v for k, v in tok.items() if "token" not in k}, indent=2))
    save_auth(tok)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("device", help="start device flow, print URL, poll, save tokens")
    sp = sub.add_parser("register")
    sp.add_argument("email"); sp.add_argument("password")
    sp.add_argument("--challenge", default="", help="hCaptcha challengeToken (required server-side)")
    sp = sub.add_parser("login")
    sp.add_argument("email"); sp.add_argument("password")
    sp.add_argument("--challenge", default="")
    sp = sub.add_parser("approve")
    sp.add_argument("user_code")
    a = p.parse_args()
    if a.cmd == "device":
        cmd_flow(a)
    elif a.cmd == "register":
        register(a.email, a.password, a.challenge)
    elif a.cmd == "login":
        login(a.email, a.password, a.challenge)
    elif a.cmd == "approve":
        cookie = (Path.home() / ".config/auto-freecf/opencode_session.txt").read_text().strip()
        approve(a.user_code, cookie)


if __name__ == "__main__":
    main()
