#!/usr/bin/env python3
"""mysterium_client.py — Standalone Python Client for Mysterium Residential VPN API.

Reverse-engineered from Mysterium Chrome Extension & OpenAPI 3.0 specs.
Provides node discovery, country/city filtering, and proxy connection management.

API Base: https://api.mysteriumvpn.com/api/v1
Location Base: https://location.mysterium.network/api/v1

Usage:
  python3 scripts/mysterium_client.py locations [--country US]
  python3 scripts/mysterium_client.py ip
  python3 scripts/mysterium_client.py activate
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

API_BASE = "https://api.mysteriumvpn.com/api/v1"
LOCATION_BASE = "https://location.mysterium.network/api/v1"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) MysteriumExtension/0.2.0 Chrome/130.0"

CONFIG_DIR = Path.home() / ".config/auto-freecf"
TOKEN_FILE = CONFIG_DIR / "mysterium_token.json"


def _api_request(
    endpoint: str,
    method: str = "GET",
    body: dict | None = None,
    token: str = "",
    timeout: int = 15,
) -> dict:
    url = f"{API_BASE}{endpoint}" if endpoint.startswith("/") else endpoint
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content = resp.read().decode("utf-8", "ignore")
        return json.loads(content) if content else {}


def get_current_location() -> dict:
    """Detect current IP, ISP, and node type via Mysterium telemetry."""
    url = f"{LOCATION_BASE}/location"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8", "ignore"))


def get_locations(country_filter: str | None = None) -> list[dict]:
    """Retrieve full live list of Mysterium nodes across all countries."""
    data = _api_request("/connection/config/locations")
    if not isinstance(data, list):
        return []

    if country_filter:
        cf = country_filter.upper()
        return [c for c in data if c.get("country", "").upper() == cf]
    return data


def create_activation_session() -> dict:
    """Create a device session activation request."""
    session_id = str(uuid.uuid4())
    res = _api_request("/auth/activation", method="POST", body={"id": session_id})
    return {"session_id": session_id, "result": res}


def check_activation_status(session_id: str) -> dict:
    """Poll activation status for an activation session."""
    return _api_request(f"/auth/activation/{session_id}", method="GET")


def connect_proxy(token: str, country: str = "US", ip_type: str = "residential") -> dict:
    """Request proxy configuration from Mysterium (requires Bearer token)."""
    payload = {
        "country": country.upper(),
        "ip_type": ip_type,
        "os_type": "web",
        "reset_connection": True,
    }
    return _api_request("/connection/connect-proxy", method="POST", body=payload, token=token)


def main():
    parser = argparse.ArgumentParser(description="Mysterium Residential VPN API Client")
    subparsers = parser.add_subparsers(dest="subcommand", help="Command to run")

    # Command: locations
    loc_parser = subparsers.add_parser("locations", help="List available node locations")
    loc_parser.add_argument("--country", default="", help="Filter by country code (e.g. US, JP, DE)")

    # Command: ip
    subparsers.add_parser("ip", help="Check current IP and ISP via Mysterium telemetry")

    # Command: activate
    subparsers.add_parser("activate", help="Start a new device activation session")

    # Command: connect
    conn_parser = subparsers.add_parser("connect", help="Request proxy connection config")
    conn_parser.add_argument("--country", default="US", help="Target country code (default: US)")
    conn_parser.add_argument("--type", default="residential", help="residential | hosting | cellular")
    conn_parser.add_argument("--token", default="", help="Bearer token (optional)")

    args = parser.parse_args()

    if args.subcommand == "ip":
        print("[*] Querying Mysterium Location Engine...")
        loc = get_current_location()
        print(f"IP       : {loc.get('ip')}")
        print(f"Country  : {loc.get('country')} ({loc.get('city')}, {loc.get('region')})")
        print(f"ISP      : {loc.get('isp')} (ASN: {loc.get('asn')})")
        print(f"Node Type: {loc.get('node_type')}")

    elif args.subcommand == "locations":
        print(f"[*] Fetching node locations (filter={args.country or 'ALL'})...")
        locs = get_locations(country_filter=args.country or None)
        print(f"\n{'Country':<10} {'Nodes':<10} {'Cities':<10}")
        print("-" * 35)
        for item in sorted(locs, key=lambda x: x.get("total", 0), reverse=True)[:25]:
            c_code = item.get("country", "")
            total = item.get("total", 0)
            cities_cnt = len(item.get("cities", []))
            print(f"{c_code:<10} {total:<10} {cities_cnt:<10}")

    elif args.subcommand == "activate":
        print("[*] Creating activation session on Mysterium...")
        act = create_activation_session()
        sid = act["session_id"]
        print(f"[+] Session UUID: {sid}")
        status = check_activation_status(sid)
        print(f"    Status: {status}")

    elif args.subcommand == "connect":
        tok = args.token or (json.load(open(TOKEN_FILE)).get("token") if TOKEN_FILE.exists() else "")
        if not tok:
            print("Error: No Bearer token available. Pass via --token.")
            sys.exit(1)
        print(f"[*] Requesting {args.type} proxy in {args.country}...")
        try:
            res = connect_proxy(tok, country=args.country, ip_type=args.type)
            print("Connection Config:", json.dumps(res, indent=2))
        except urllib.error.HTTPError as e:
            print(f"HTTP Error {e.code}: {e.read().decode()}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
