#!/usr/bin/env python3
"""Mozilla Guardian Client for Firefox Built-in VPN (50 GB Egress).

Exchanges a Firefox Account (FxA) OAuth token for a Fastly ProxyPass JWT
via the Guardian API (https://vpn.mozilla.org/api/v1/fpn/token), tracks
the 50 GB monthly bandwidth quota, and saves the token for fastly_vpn_proxy.py.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

GUARDIAN_ENDPOINT = "https://vpn.mozilla.org"
CONFIG_DIR = Path.home() / ".config" / "auto-freecf"
TOKEN_FILE = CONFIG_DIR / "fastly_vpn_token.txt"
FXA_TOKEN_FILE = CONFIG_DIR / "fxa_oauth_token.txt"


def activate_user(fxa_token: str) -> dict:
    """Enroll and activate the user on Mozilla Guardian."""
    url = f"{GUARDIAN_ENDPOINT}/api/v1/fpn/activate"
    req = urllib.request.Request(
        url,
        data=b"{}",
        headers={
            "Authorization": f"Bearer {fxa_token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (rv:158.0) Gecko/20100101 Firefox/158.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode()
            return json.loads(body) if body else {"ok": True}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode(errors="ignore")
        return {"error": f"HTTP {e.code}: {err_body}"}
    except Exception as e:
        return {"error": str(e)}


def fetch_proxy_token(fxa_token: str) -> dict:
    """Fetch Fastly ProxyPass JWT and usage stats from Guardian."""
    url = f"{GUARDIAN_ENDPOINT}/api/v1/fpn/token"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {fxa_token}",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (rv:158.0) Gecko/20100101 Firefox/158.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            headers = dict(resp.headers)
            body = resp.read().decode()

            token = ""
            # Response may be JSON {"token": "..."} or raw JWT
            try:
                data = json.loads(body)
                token = data.get("token") or data.get("pass") or body.strip()
            except Exception:
                token = body.strip()

            usage = {
                "remaining": headers.get("x-vpn-usage-remaining"),
                "max": headers.get("x-vpn-usage-max"),
                "reset_time": headers.get("x-vpn-usage-reset-time"),
            }
            return {"token": token, "usage": usage, "status": resp.status}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode(errors="ignore")
        return {"error": f"HTTP {e.code}: {err_body}", "status": e.code}
    except Exception as e:
        return {"error": str(e)}


def decode_jwt_claims(jwt_str: str) -> dict:
    """Parse JWT claims without signature verification."""
    try:
        parts = jwt_str.split(".")
        if len(parts) >= 2:
            padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
            return json.loads(base64.urlsafe_b64decode(padded).decode())
    except Exception:
        pass
    return {}


def save_token(token: str):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token.strip())
    print(f"Token saved to {TOKEN_FILE}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mozilla Guardian Token Client")
    parser.add_argument("--fxa-token", type=str, help="FxA OAuth Token")
    parser.add_argument("--activate", action="store_true", help="Run /fpn/activate first")
    args = parser.parse_args()

    fxa_tok = (
        args.fxa_token
        or os.environ.get("FXA_OAUTH_TOKEN", "")
        or (FXA_TOKEN_FILE.read_text().strip() if FXA_TOKEN_FILE.exists() else "")
    )

    if not fxa_tok:
        print("Error: No FxA OAuth token supplied.")
        print("Provide via --fxa-token, $FXA_OAUTH_TOKEN, or ~/.config/auto-freecf/fxa_oauth_token.txt")
        sys.exit(1)

    if args.activate:
        print("Activating account on Guardian...")
        act_res = activate_user(fxa_tok)
        print("Activation result:", act_res)

    print("Fetching Fastly proxy pass...")
    res = fetch_proxy_token(fxa_tok)
    if "error" in res:
        print(f"Failed to fetch proxy token: {res['error']}")
        sys.exit(1)

    token = res.get("token")
    if token:
        claims = decode_jwt_claims(token)
        exp = claims.get("exp", 0)
        exp_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(exp)) if exp else "Unknown"
        print(f"\n[+] ProxyPass JWT obtained successfully!")
        print(f"    Expires: {exp_str}")
        if claims:
            print(f"    Subject / ID: {claims.get('sub')}")
            print(f"    Issuer: {claims.get('iss')}")
        print(f"    Usage Stats: {res.get('usage')}")
        save_token(token)
    else:
        print("No token in response:", res)
