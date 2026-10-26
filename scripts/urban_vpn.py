#!/usr/bin/env python3
"""scripts/urban_vpn.py — Standalone Python Client for UrbanVPN Residential Proxy API.

Reverse-engineered from Urban VPN Chrome Extension v5.14.4 (service-worker/index.js):
  1. POST /registrations/.../users/anonymous -> Anonymous User Token
  2. POST /security/tokens/accs              -> JWT Security Token
  3. GET  /entrypoints/countries             -> 85 Countries + Proxies + Signatures
  4. POST /security/tokens/accs-proxy        -> Per-Server Proxy Token
  5. Proxy Auth: username=<accs-proxy-token>, password="1"

Usage:
  python3 scripts/urban_vpn.py get [--country JP]
  python3 scripts/urban_vpn.py list
  python3 scripts/urban_vpn.py test [--country US]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CACHE_DIR = Path.home() / ".cache" / "auto-freecf"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CACHE_FILE = CACHE_DIR / "urban_vpn_session.json"

API_PRO = "https://api-pro.urban-vpn.com/rest/v1"
API_STATS = "https://stats.urban-vpn.com/api/rest/v2"
CLIENT_APP = "URBAN_VPN_BROWSER_EXTENSION"


def _http_post(url: str, payload: dict, token: str = "") -> dict:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Client-App": CLIENT_APP,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read().decode("utf-8"))


def _http_get(url: str, token: str = "") -> dict:
    headers = {
        "Accept": "application/json",
        "X-Client-App": CLIENT_APP,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=12) as r:
        return json.loads(r.read().decode("utf-8"))


def get_fresh_session() -> dict:
    """Perform full anonymous registration & security token exchange."""
    # 1. Register anonymous user
    reg_url = f"{API_PRO}/registrations/clientApps/{CLIENT_APP}/users/anonymous"
    anon_resp = _http_post(reg_url, {"clientApp": {"name": CLIENT_APP, "browser": "CHROME"}})
    anon_tok = anon_resp["value"]

    # 2. Get security token (JWT)
    sec_url = f"{API_PRO}/security/tokens/accs"
    sec_resp = _http_post(sec_url, {"type": "accs", "clientApp": {"name": CLIENT_APP}}, token=anon_tok)
    sec_tok = sec_resp["value"]
    exp = sec_resp.get("expirationTime", time.time() * 1000 + 3600000) / 1000.0

    session = {
        "anon_tok": anon_tok,
        "sec_tok": sec_tok,
        "expires_at": exp,
    }
    CACHE_FILE.write_text(json.dumps(session, indent=2))
    return session


def get_active_session() -> dict:
    if CACHE_FILE.exists():
        try:
            sess = json.loads(CACHE_FILE.read_text())
            if sess.get("expires_at", 0) - time.time() > 300:
                return sess
        except Exception:
            pass
    return get_fresh_session()


def get_countries(sec_tok: str) -> list[dict]:
    url = f"{API_STATS}/entrypoints/countries"
    data = _http_get(url, token=sec_tok)
    return data.get("countries", {}).get("elements", [])


def get_proxy(country: str = "JP") -> dict:
    """Fetch an active residential proxy for the specified ISO country code."""
    sess = get_active_session()
    sec_tok = sess["sec_tok"]

    try:
        elements = get_countries(sec_tok)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            sess = get_fresh_session()
            sec_tok = sess["sec_tok"]
            elements = get_countries(sec_tok)
        else:
            raise

    country = country.upper()
    chosen_server = None
    for el in elements:
        code = el.get("code", {}).get("iso2", "").upper()
        if code == country and el.get("servers", {}).get("elements"):
            chosen_server = el["servers"]["elements"][0]
            break

    if not chosen_server:
        # Fallback to first available country
        for el in elements:
            if el.get("servers", {}).get("elements"):
                chosen_server = el["servers"]["elements"][0]
                country = el.get("code", {}).get("iso2", "").upper()
                break

    if not chosen_server:
        raise RuntimeError(f"No proxy servers available for {country}")

    primary = chosen_server["address"]["primary"]
    host = primary["host"]
    port = primary["port"]
    sig = chosen_server["signature"]

    # Exchange signature for proxy credential token
    tok_url = f"{API_PRO}/security/tokens/accs-proxy"
    proxy_resp = _http_post(
        tok_url,
        {"type": "accs-proxy", "clientApp": {"name": CLIENT_APP}, "signature": sig},
        token=sec_tok,
    )
    proxy_user = proxy_resp["value"]
    proxy_pass = "1"
    proxy_url = f"http://{proxy_user}:{proxy_pass}@{host}:{port}"

    return {
        "provider": "urban_vpn",
        "country": country,
        "name": chosen_server.get("name"),
        "host": host,
        "port": port,
        "username": proxy_user,
        "password": proxy_pass,
        "proxy": proxy_url,
    }


def main():
    ap = argparse.ArgumentParser(description="UrbanVPN Residential Proxy Client")
    sub = ap.add_subparsers(dest="cmd", required=True)

    get_p = sub.add_parser("get", help="Get a residential proxy")
    get_p.add_argument("--country", default="JP", help="2-letter ISO country code (e.g. JP, US, DE)")

    sub.add_parser("list", help="List available country codes")

    test_p = sub.add_parser("test", help="Test proxy connectivity via curl")
    test_p.add_argument("--country", default="JP", help="Country code to test")

    args = ap.parse_args()

    if args.cmd == "get":
        px = get_proxy(args.country)
        print(json.dumps(px, indent=2))
    elif args.cmd == "list":
        sess = get_active_session()
        elements = get_countries(sess["sec_tok"])
        codes = [el.get("code", {}).get("iso2") for el in elements if el.get("servers", {}).get("elements")]
        print(f"Available countries ({len(codes)}):", ", ".join(sorted(codes)))
    elif args.cmd == "test":
        px = get_proxy(args.country)
        print(f"Connecting through {px['country']} ({px['host']}:{px['port']})...")
        import subprocess
        res = subprocess.run(["curl", "-s", "-m", "15", "-x", px["proxy"], "https://api.ipify.org?format=json"], capture_output=True, text=True)
        print("Response:", res.stdout.strip())


if __name__ == "__main__":
    main()
