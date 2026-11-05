#!/usr/bin/env python3
"""scripts/orvis_client.py — Native Python client and account harvester for Orvis AI.

Orvis AI provides 100 free credits per account for frontier models:
Claude Sonnet 4, Claude Opus 4, OpenAI o3-mini, GPT-4o, and Gemini 2.5 Flash.

Architecture:
- AWS Cognito Identity Provider (ap-south-1) for signup/auth.
- Egress protected by residential proxy (UrbanVPN) to bypass AWS WAF.
- Email verification via mailboxes.py multi-tier fallback (Tempik -> InboxKitten -> Guerrilla).
- Streaming chat via authenticated WebSocket (wss://api.orvisai.com/api/v1/ws/chat).
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import re
import secrets
import string
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import mailboxes
import proxy_pool

COGNITO_ENDPOINT = "https://cognito-idp.ap-south-1.amazonaws.com/"
COGNITO_CLIENT_ID = "3u3kbgsnaf7dclfbjfaglek8ce"
ORVIS_API_BASE = "https://api.orvisai.com/api/v1"
ORVIS_WS_URL = "wss://api.orvisai.com/api/v1/ws/chat"

ACCOUNTS_FILE = Path.home() / ".config" / "auto-freecf" / "orvis_accounts.json"

MODEL_MAP = {
    # Anthropic
    "claude-sonnet-4": ("anthropic", "claude-sonnet-4-20250514"),
    "claude-opus-4": ("anthropic", "claude-opus-4-20250514"),
    "claude-haiku-4.5": ("anthropic", "claude-haiku-4-5-20251001"),
    # OpenAI
    "gpt-4o": ("openai", "gpt-4o"),
    "gpt-4o-mini": ("openai", "gpt-4o-mini"),
    "o3-mini": ("openai", "o3-mini"),
    # Google
    "gemini-2.5-pro": ("google", "gemini-2.5-pro"),
    "gemini-2.5-flash": ("google", "gemini-2.5-flash"),
    "gemini-2.0-flash": ("google", "gemini-2.0-flash"),
    # Default
    "default": ("google", "gemini-2.5-flash"),
}


def _get_proxy_url() -> str | None:
    """Get authenticated proxy URL from residential pool, or fallback to local rotating gateway."""
    if os.environ.get("HTTP_PROXY"):
        return os.environ.get("HTTP_PROXY")
    try:
        info = proxy_pool.best()
        p = info.get("proxy") or {}
        srv = p.get("server", "").replace("http://", "").replace("https://", "")
        if srv:
            user = p.get("username")
            pwd = p.get("password")
            if user and pwd:
                return f"http://{user}:{pwd}@{srv}"
            return f"http://{srv}"
    except Exception:
        pass
    # Fallback to local rotating residential gateway
    return "http://127.0.0.1:8899"

def _cognito_rpc(target: str, payload: dict, proxy_url: str | None = None, timeout: int = 25) -> dict:
    """Execute raw JSON-RPC call to AWS Cognito."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        COGNITO_ENDPOINT,
        data=data,
        headers={
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": f"AWSCognitoIdentityProviderService.{target}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
    )
    proxy = proxy_url if proxy_url is not None else _get_proxy_url()
    if proxy:
        handler = urllib.request.ProxyHandler({"https": proxy, "http": proxy})
        opener = urllib.request.build_opener(handler)
    else:
        opener = urllib.request.build_opener()
    try:
        with opener.open(req, timeout=timeout) as r:
            body = r.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(err_body)
            msg = parsed.get("message") or parsed.get("__type") or err_body
        except Exception:
            msg = err_body
        raise RuntimeError(f"Cognito {target} failed (HTTP {e.code}): {msg}") from e


def generate_password() -> str:
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    return (
        secrets.choice(string.ascii_uppercase)
        + secrets.choice(string.ascii_lowercase)
        + secrets.choice(string.digits)
        + secrets.choice("!@#$%^&*")
        + "".join(secrets.choice(chars) for _ in range(12))
    )


def create_account(log: Callable[[str], None] = print) -> dict:
    """Create a verified Orvis account, claim 100 free credits, and return tokens."""
    log("  [orvis] opening mailbox with auto-fallback...")
    box = mailboxes.open_mailbox("auto", log=log)
    email = box.address
    password = generate_password()
    name = f"User {secrets.token_hex(3)}"
    proxy = _get_proxy_url()
    log(f"  [orvis] mailbox: {email}")
    log(f"  [orvis] registering via Cognito through residential proxy...")

    res_signup = _cognito_rpc(
        "SignUp",
        {
            "ClientId": COGNITO_CLIENT_ID,
            "Username": email,
            "Password": password,
            "UserAttributes": [{"Name": "email", "Value": email}, {"Name": "name", "Value": name}],
        },
        proxy_url=proxy,
    )
    user_sub = res_signup.get("UserSub", "")
    log(f"  [orvis] signup submitted (sub: {user_sub[:8]}...), waiting for OTP...")

    code = box.code(vendor="orvis", timeout=90) or box.code(timeout=60)
    if not code:
        raise RuntimeError("verification OTP never arrived in mailbox")
    log(f"  [orvis] OTP received: {code}, confirming...")

    _cognito_rpc(
        "ConfirmSignUp",
        {"ClientId": COGNITO_CLIENT_ID, "Username": email, "ConfirmationCode": code},
        proxy_url=proxy,
    )
    log("  [orvis] confirmation complete. logging in...")

    auth_res = _cognito_rpc(
        "InitiateAuth",
        {
            "ClientId": COGNITO_CLIENT_ID,
            "AuthFlow": "USER_PASSWORD_AUTH",
            "AuthParameters": {"USERNAME": email, "PASSWORD": password},
        },
        proxy_url=proxy,
    )
    tokens = auth_res.get("AuthenticationResult", {})
    access_token = tokens.get("AccessToken", "")
    id_token = tokens.get("IdToken", "")
    refresh_token = tokens.get("RefreshToken", "")

    # Fetch initial balance
    req_bal = urllib.request.Request(
        f"{ORVIS_API_BASE}/credits/balance",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
    proxy = _get_proxy_url()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy, "http": proxy})) if proxy else urllib.request.build_opener()
    with opener.open(req_bal, timeout=20) as r:
        bal_data = json.loads(r.read().decode("utf-8"))
    balance = bal_data.get("balance", 100.0)
    sender_user_id = bal_data.get("sender_user_id") or user_sub

    record = {
        "email": email,
        "password": password,
        "sender_user_id": sender_user_id,
        "access_token": access_token,
        "id_token": id_token,
        "refresh_token": refresh_token,
        "balance": balance,
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    _save_account(record)
    log(f"  [orvis] ✓ account created! balance: {balance} credits (sender_id: {sender_user_id[:8]}...)")
    return record


def _save_account(acct: dict) -> None:
    ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    accounts = load_accounts()
    # update or append
    for i, a in enumerate(accounts):
        if a.get("email") == acct.get("email"):
            accounts[i] = acct
            break
    else:
        accounts.append(acct)
    ACCOUNTS_FILE.write_text(json.dumps(accounts, indent=2))


def load_accounts() -> list[dict]:
    if not ACCOUNTS_FILE.is_file():
        return []
    try:
        return json.loads(ACCOUNTS_FILE.read_text())
    except Exception:
        return []


def refresh_account_tokens(acct: dict) -> dict:
    """Refresh Cognito tokens using refresh_token."""
    rt = acct.get("refresh_token")
    if not rt:
        return acct
    proxy = _get_proxy_url()
    try:
        res = _cognito_rpc(
            "InitiateAuth",
            {
                "ClientId": COGNITO_CLIENT_ID,
                "AuthFlow": "REFRESH_TOKEN_AUTH",
                "AuthParameters": {"REFRESH_TOKEN": rt},
            },
            proxy_url=proxy,
        )
        tokens = res.get("AuthenticationResult", {})
        if tokens.get("AccessToken"):
            acct["access_token"] = tokens["AccessToken"]
        if tokens.get("IdToken"):
            acct["id_token"] = tokens["IdToken"]
        acct["updated_at"] = time.time()
        _save_account(acct)
    except Exception as e:
        print(f"  [orvis] token refresh error: {e}")
    return acct


def get_usable_account() -> dict:
    """Return an account with positive balance; auto-create if none available."""
    accounts = load_accounts()
    for a in accounts:
        if a.get("balance", 0) > 0.5 and a.get("access_token"):
            # Auto-refresh if token older than 45 minutes (Cognito expiry is 60m)
            updated = a.get("updated_at", 0)
            if time.time() - updated > 2700:
                a = refresh_account_tokens(a)
            return a
    print("  [orvis] no account with active balance found — harvesting fresh account...")
    return create_account()


async def chat_turn(
    prompt: str,
    model_name: str = "gemini-2.5-flash",
    account: dict | None = None,
    on_delta: Optional[Callable[[str], None]] = None,
    timeout: int = 120,
) -> str:
    """Execute a single chat turn via Orvis WebSocket."""
    import websockets

    acct = account or get_usable_account()
    agent_provider, agent_model = MODEL_MAP.get(model_name, MODEL_MAP["default"])

    async with websockets.connect(ORVIS_WS_URL) as ws:
        init_frame = {
            "type": "init",
            "access_token": acct.get("access_token", ""),
            "id_token": acct.get("id_token", ""),
            "refresh_token": acct.get("refresh_token", ""),
            "workspace_id": "",
            "sender_user_id": acct.get("sender_user_id", ""),
            "recipient_user_id": "",
            "organisation_id": "",
            "channel_id": "",
            "name": "Kanca User",
            "email": acct.get("email", ""),
            "phone": "",
            "client_platform": "web",
            "skills": [],
            "tools": [],
            "agent_instructions": "",
            "use_custom_prompt_only": False,
            "agent_references": [],
            "delete_stale_refs": False,
            "sandbox_internet": False,
            "sandbox_ports": [],
            "sandbox_vnc": False,
            "session_id": "",
            "last_seen_seq": 0,
            "timezone": "UTC",
            "model_config": {
                "agent_provider": agent_provider,
                "agent_model": agent_model,
            },
            "thinking_enabled": False,
        }
        await ws.send(json.dumps(init_frame))

        session_id = ""
        chunks = []
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            f = json.loads(raw)
            ftype = f.get("type")
            if ftype == "init_ack":
                session_id = f.get("session_id", "")
                chat_msg = {
                    "type": "message",
                    "message": prompt,
                    "conversation_id": "",
                    "fast_mode": False,
                    "message_id": f"g{int(time.time() * 1e9)}",
                    "message_hash": "",
                    "session_id": session_id,
                    "attachments": [],
                    "mode": "chat",
                    "media_prefs": {},
                    "template_pick": None,
                }
                await ws.send(json.dumps(chat_msg))
            elif ftype == "text":
                c = f.get("content", "")
                chunks.append(c)
                if on_delta:
                    on_delta(c)
            elif ftype == "done":
                break
            elif ftype == "auth_error":
                raise RuntimeError("Orvis auth error: session expired or token unauthorized. Token refresh required.")
            elif ftype in ("error", "failed"):
                raise RuntimeError(f"Orvis chat error: {f}")
            elif ftype == "ping":
                await ws.send(json.dumps({"type": "pong"}))
        return "".join(chunks)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Orvis AI account harvester & chat client")
    ap.add_argument("cmd", choices=["create", "list", "chat", "balance"], default="balance", nargs="?")
    ap.add_argument("-m", "--model", default="gemini-2.5-flash", help="model name")
    ap.add_argument("-p", "--prompt", default="respond with only: pong", help="chat prompt")
    args = ap.parse_args()

    if args.cmd == "create":
        ac = create_account()
        print(f"Created account: {ac['email']} (balance: {ac['balance']})")
    elif args.cmd == "list":
        accs = load_accounts()
        print(f"Total harvested accounts: {len(accs)}")
        for a in accs:
            print(f" - {a.get('email')}: balance={a.get('balance')}, sub={a.get('sender_user_id', '')[:8]}")
    elif args.cmd == "chat":
        print(f"Running chat turn ({args.model})...")
        ans = asyncio.run(chat_turn(args.prompt, model_name=args.model))
        print("Response:", ans)
    elif args.cmd == "balance":
        accs = load_accounts()
        if not accs:
            print("No accounts found. Use 'create' first.")
        else:
            for a in accs:
                print(f"Account: {a.get('email')} -> {a.get('balance')} credits")
