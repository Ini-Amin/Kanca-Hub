#!/usr/bin/env python3
"""
KancaHub region profiles — pick where signups egress for promos/bonuses.

Cloudflare treats accounts differently by region (free-tier generosity, promo
availability, datacenter the account is pinned to). This module centralises
that choice so every command can honour it.

A "profile" bundles:
  - proxy_country : ISO code used to bias proxy hunting (--country)
  - warp_endpoint : optional pinned Cloudflare WARP endpoint host:port
  - label         : human name

Usage:
    python3 regions.py list
    python3 regions.py show us
    python3 regions.py set us          # writes ~/.config/auto-freecf/region.json
    python3 regions.py current
    python3 regions.py clear
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

CONFIG = Path.home() / ".config" / "auto-freecf" / "region.json"

# Cloudflare has a small set of well-known WARP/WireGuard endpoints. Pinning one
# biases traffic toward that anycast region (helpful, not a hard guarantee).
PROFILES: dict[str, dict] = {
    "us":  {"label": "United States", "proxy_country": "US", "warp_endpoint": "162.159.192.1:2408"},
    "uk":  {"label": "United Kingdom", "proxy_country": "GB", "warp_endpoint": "162.159.192.1:2408"},
    "sg":  {"label": "Singapore",     "proxy_country": "SG", "warp_endpoint": "162.159.193.10:2408"},
    "id":  {"label": "Indonesia",     "proxy_country": "ID", "warp_endpoint": "162.159.193.10:2408"},
    "de":  {"label": "Germany",       "proxy_country": "DE", "warp_endpoint": "162.159.192.1:2408"},
    "jp":  {"label": "Japan",         "proxy_country": "JP", "warp_endpoint": "162.159.193.10:2408"},
    "in":  {"label": "India",         "proxy_country": "IN", "warp_endpoint": "162.159.193.10:2408"},
    "br":  {"label": "Brazil",        "proxy_country": "BR", "warp_endpoint": "162.159.192.1:2408"},
    "au":  {"label": "Australia",     "proxy_country": "AU", "warp_endpoint": "162.159.193.10:2408"},
    "ca":  {"label": "Canada",        "proxy_country": "CA", "warp_endpoint": "162.159.192.1:2408"},
    "any": {"label": "Auto (nearest)","proxy_country": None, "warp_endpoint": None},
}


def load() -> dict:
    if CONFIG.exists():
        try:
            return json.loads(CONFIG.read_text())
        except Exception:
            pass
    return {"profile": "any"}


def current() -> dict:
    name = load().get("profile", "any")
    p = dict(PROFILES.get(name, PROFILES["any"]))
    p["name"] = name
    return p


def set_profile(name: str) -> bool:
    if name not in PROFILES:
        return False
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps({"profile": name}, indent=2) + "\n")
    return True


def clear() -> None:
    if CONFIG.exists():
        CONFIG.unlink()


def main() -> int:
    ap = argparse.ArgumentParser(description="KancaHub signup region profiles")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("list")
    s = sub.add_parser("show"); s.add_argument("name")
    s2 = sub.add_parser("set"); s2.add_argument("name")
    sub.add_parser("current")
    sub.add_parser("clear")
    a = ap.parse_args()

    if a.cmd == "list":
        cur = load().get("profile", "any")
        for name, p in PROFILES.items():
            mark = "●" if name == cur else " "
            print(f"  {mark} {name:5s} {p['label']:16s} country={p['proxy_country'] or '-':4s} endpoint={p['warp_endpoint'] or '-'}")
        return 0
    if a.cmd == "show":
        print(json.dumps(PROFILES.get(a.name, {}), indent=2)); return 0 if a.name in PROFILES else 1
    if a.cmd == "set":
        if set_profile(a.name):
            print(f"✓ region profile -> {a.name} ({PROFILES[a.name]['label']})")
            return 0
        print(f"✗ unknown profile: {a.name}", file=sys.stderr); return 1
    if a.cmd == "clear":
        clear(); print("✓ region profile cleared (auto)"); return 0
    # current (default)
    c = current()
    print(f"  profile : {c['name']} ({c['label']})")
    print(f"  country : {c['proxy_country'] or 'auto'}")
    print(f"  endpoint: {c['warp_endpoint'] or 'auto'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
