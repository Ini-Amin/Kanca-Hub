#!/usr/bin/env python3
"""tuxler_client.py — Standalone Python Client for Tuxler Residential VPN API.

Reverse-engineered from Tuxler Chrome Extension 3.1.26 unminified source maps
(artifacts/re_tuxler/src/src/js/api.js & app-local.js).

Tuxler provides community-powered residential SOCKS5 proxies per country/city.

API Base: https://api.tuxler.com/ext_api.php?action=...

Usage:
  python3 scripts/tuxler_client.py get [--country US] [--city Miami]
  python3 scripts/tuxler_client.py startup
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

API_URL = "https://api.tuxler.com/ext_api.php?action="
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

CONFIG_DIR = Path.home() / ".config/auto-freecf"
TUXLER_CREDENTIALS = CONFIG_DIR / "tuxler_account.json"


def get_unique_id() -> str:
    """Stable per-installation application unique ID."""
    uid_file = CONFIG_DIR / "tuxler_uid.txt"
    if uid_file.exists():
        return uid_file.read_text().strip()
    new_uid = str(uuid.uuid4()).replace("-", "")[:24]
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    uid_file.write_text(new_uid)
    return new_uid


def api_call(action: str, params: dict | None = None, timeout: int = 15) -> dict:
    """Execute API call to api.tuxler.com with query string parameters and app_unique_id."""
    params = dict(params or {})
    params["app_unique_id"] = get_unique_id()

    query_str = urllib.parse.urlencode(params)
    url = f"{API_URL}{action}&{query_str}"

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Origin": "chrome-extension://jpgljfpmoofbmlieejglhonfofmahini",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content = resp.read().decode("utf-8", "ignore")
        return json.loads(content) if content else {}


def get_startup_data(email: str = "", password: str = "") -> dict:
    return api_call("STARTUP_DATA", {"username": email, "password": password})


def get_new_server(email: str = "", password: str = "", country_code: str = "", city_name: str = "") -> dict:
    params = {"username": email, "password": password}
    if country_code:
        params["country_code"] = country_code.upper()
    if city_name:
        params["city_name"] = city_name
    return api_call("GET_NEW_SERVER", params)


def main():
    parser = argparse.ArgumentParser(description="Tuxler Residential VPN API Client")
    subparsers = parser.add_subparsers(dest="subcommand", help="Command to run")

    # Command: startup
    subparsers.add_parser("startup", help="Check startup data and status")

    # Command: get
    get_parser = subparsers.add_parser("get", help="Request a new SOCKS5 proxy server")
    get_parser.add_argument("--country", default="", help="Country code (e.g. US, NL, GB)")
    get_parser.add_argument("--city", default="", help="City name (optional)")
    get_parser.add_argument("--email", default="", help="Tuxler account email (optional)")
    get_parser.add_argument("--password", default="", help="Tuxler account password (optional)")

    args = parser.parse_args()

    email = getattr(args, "email", "") or (json.load(open(TUXLER_CREDENTIALS)).get("email") if TUXLER_CREDENTIALS.exists() else "")
    password = getattr(args, "password", "") or (json.load(open(TUXLER_CREDENTIALS)).get("password") if TUXLER_CREDENTIALS.exists() else "")

    if args.subcommand == "startup":
        print("[*] Fetching Tuxler Startup Data...")
        res = get_startup_data(email, password)
        print("Response:", json.dumps(res, indent=2))

    elif args.subcommand == "get":
        print(f"[*] Requesting SOCKS5 server (country={args.country or 'ANY'}, city={args.city or 'ANY'})...")
        res = get_new_server(email, password, country_code=args.country, city_name=args.city)
        print("Server Info:", json.dumps(res, indent=2))
        if res.get("status") == "OK" and "data" in res:
            srv = res["data"]
            print(f"\n[+] SOCKS5 Node Allocated:")
            print(f"    Host   : {srv.get('hostname')}:{srv.get('port')}")
            print(f"    Country: {srv.get('country_name')} ({srv.get('country_code')})")
            print(f"    City   : {srv.get('city_name')}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
