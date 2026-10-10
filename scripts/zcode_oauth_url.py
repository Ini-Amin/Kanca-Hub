#!/usr/bin/env python3
"""
scripts/zcode_oauth_url.py — recreate the ZCode<->z.ai OAuth "signup/login" URL.

Captured pattern (ZCode 3.14.5 app, stable across many runs):
  https://chat.z.ai/auth
    ?response_type=code
    &client_id=client_P8X5CMWmlaRO9gyO-KSqtg
    &redirect_uri=https://zcode.z.ai/app/oauth/login?redirect=zcode://oauth/callback
    &app_version=3.14.5
    &state=<random>

Only `state` varies (it's a nonce we can pick ourselves), so the whole URL is
reproducible WITHOUT the desktop app. Completing it (sign in on chat.z.ai ->
consent) redirects to zcode.z.ai/app/oauth/login?code=... which mints the ZCode
session (the same thing the app does).

Usage:
  python scripts/zcode_oauth_url.py                 # print one authorize URL
  python scripts/zcode_oauth_url.py --n 5           # a batch ("farm")
"""
from __future__ import annotations

import argparse
import secrets
import urllib.parse

CLIENT_ID = "client_P8X5CMWmlaRO9gyO-KSqtg"
REDIRECT_INNER = "zcode://oauth/callback"
REDIRECT_URI = "https://zcode.z.ai/app/oauth/login?redirect=" + urllib.parse.quote(REDIRECT_INNER, safe="")
APP_VERSION = "3.14.5"


def make_url(state: str | None = None) -> str:
    state = state or secrets.token_hex(16)
    q = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "app_version": APP_VERSION,
        "state": state,
    }
    return "https://chat.z.ai/auth?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Recreate the ZCode OAuth 'signup' URL(s)")
    ap.add_argument("--n", type=int, default=1, help="how many to print")
    a = ap.parse_args(argv)
    for _ in range(a.n):
        print(make_url())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())