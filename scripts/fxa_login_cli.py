#!/usr/bin/env python3
"""FxA OAuth Login Helper for Mozilla VPN (50 GB Egress).

Generates PKCE challenge and authorization URL for Firefox Accounts with the
VPN scope ('profile https://identity.mozilla.com/apps/vpn').
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import urllib.parse
import urllib.request
from pathlib import Path

# Public Android / Fenix Client ID
CLIENT_ID = "e7ce535d93522896"
SCOPE = "profile https://identity.mozilla.com/apps/vpn"
REDIRECT_URI = "https://lockbox.firefox.com/fxa/android-redirect.html"

AUTH_ENDPOINT = "https://accounts.firefox.com/authorization"
TOKEN_ENDPOINT = "https://oauth.accounts.firefox.com/v1/token"

CONFIG_DIR = Path.home() / ".config" / "auto-freecf"
FXA_TOKEN_FILE = CONFIG_DIR / "fxa_oauth_token.txt"


def base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def generate_pkce() -> tuple[str, str]:
    verifier = base64url_encode(secrets.token_bytes(32))
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64url_encode(digest)
    return verifier, challenge


def get_auth_url(code_challenge: str, state: str) -> str:
    params = {
        "client_id": CLIENT_ID,
        "scope": SCOPE,
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
        "access_type": "offline",
    }
    return f"{AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"


def exchange_code(code: str, code_verifier: str) -> dict:
    payload = {
        "client_id": CLIENT_ID,
        "grant_type": "authorization_code",
        "code_verifier": code_verifier,
        "code": code.strip(),
    }
    req = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "exchange":
        code = sys.argv[2]
        verifier = sys.argv[3]
        print(f"Exchanging code {code[:8]}... for token...")
        try:
            tokens = exchange_code(code, verifier)
            acc_tok = tokens.get("access_token")
            if acc_tok:
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                FXA_TOKEN_FILE.write_text(acc_tok.strip())
                print("[+] Successfully received FxA access token!")
                print(f"    Saved to {FXA_TOKEN_FILE}")
            else:
                print("No access_token in response:", tokens)
        except Exception as e:
            print("Token exchange error:", e)
    else:
        verifier, challenge = generate_pkce()
        state = base64url_encode(secrets.token_bytes(16))
        url = get_auth_url(challenge, state)

        print("=== Firefox Account (FxA) Authorization URL ===")
        print("Buka URL berikut di browser kamu untuk login ke akun Firefox:")
        print(url)
        print("\nSetelah login, browser akan dialihkan ke halaman:")
        print("https://lockbox.firefox.com/fxa/android-redirect.html?code=XXXXX&state=...")
        print("\nSalin nilai parameter 'code' dari address bar, lalu jalankan:")
        print(f"python3 scripts/fxa_login_cli.py exchange <CODE> {verifier}")
