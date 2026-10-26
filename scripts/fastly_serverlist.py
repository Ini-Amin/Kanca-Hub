#!/usr/bin/env python3
"""Fastly VPN Serverlist & Latency Ranker for Mozilla IP Protection (50 GB Egress).

Fetches the official Mozilla RemoteSettings collection `vpn-serverlist` (35 regions)
and provides quick concurrent latency probing to rank the fastest nodes.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import socket
import ssl
import time
import urllib.request
from pathlib import Path

REMOTESETTINGS_URL = (
    "https://firefox.settings.services.mozilla.com/v1/buckets/main/collections/vpn-serverlist/records"
)
CACHE_DIR = Path.home() / ".config" / "auto-freecf"
CACHE_FILE = CACHE_DIR / "vpn_serverlist.json"
CACHE_TTL = 86400  # 24 hours


def fetch_serverlist(force_refresh: bool = False) -> list[dict]:
    """Fetch all VPN server records, using local disk cache if still fresh."""
    if not force_refresh and CACHE_FILE.exists():
        try:
            mtime = CACHE_FILE.stat().st_mtime
            if time.time() - mtime < CACHE_TTL:
                with open(CACHE_FILE, "r") as f:
                    data = json.load(f)
                    if data:
                        return data
        except Exception:
            pass

    req = urllib.request.Request(
        REMOTESETTINGS_URL,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:158.0) Gecko/20100101 Firefox/158.0"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = json.loads(resp.read().decode())
        records = body.get("data", [])

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(records, f, indent=2)

    return records


def parse_servers(records: list[dict]) -> list[dict]:
    """Flatten RemoteSettings records into a list of server candidates."""
    servers = []
    for rec in records:
        code = rec.get("code", "")
        country = rec.get("name", "")
        for city in rec.get("cities", []):
            city_code = city.get("code", "")
            city_name = city.get("name", country)
            for s in city.get("servers", []):
                host = s.get("hostname")
                port = s.get("port", 2499)
                if host:
                    servers.append({
                        "country_code": code,
                        "country_name": country,
                        "city_code": city_code,
                        "city_name": city_name,
                        "host": host,
                        "port": port,
                    })
    return servers


def probe_latency_ms(host: str, port: int, timeout: float = 3.0) -> float | None:
    """Measure TCP connect + TLS handshake latency in milliseconds."""
    t0 = time.perf_counter()
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                _ = ssock.cipher()
        return round((time.perf_counter() - t0) * 1000.0, 2)
    except Exception:
        return None


def rank_servers(
    country_filter: str | None = None,
    max_candidates: int = 15,
    timeout: float = 3.0,
) -> list[dict]:
    """Probe candidate servers concurrently and return ranked list (fastest first)."""
    records = fetch_serverlist()
    candidates = parse_servers(records)

    if country_filter:
        cf = country_filter.upper()
        candidates = [c for c in candidates if c["country_code"] == cf]

    # Limit probe batch
    to_probe = candidates[:max_candidates]
    ranked = []

    def _worker(srv: dict):
        lat = probe_latency_ms(srv["host"], srv["port"], timeout=timeout)
        if lat is not None:
            res = dict(srv)
            res["latency_ms"] = lat
            return res
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_worker, s) for s in to_probe]
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            if res:
                ranked.append(res)

    ranked.sort(key=lambda x: x["latency_ms"])
    return ranked


if __name__ == "__main__":
    import sys

    c_filter = sys.argv[1] if len(sys.argv) > 1 else None
    print(f"Ranking Fastly VPN nodes (filter={c_filter or 'ALL'})...")
    results = rank_servers(country_filter=c_filter, max_candidates=12)
    print(f"\nTop {len(results)} fastest healthy nodes:")
    print(f"{'Country':<15} {'City':<15} {'Host':<32} {'Latency':<10}")
    print("-" * 75)
    for r in results:
        print(
            f"{r['country_name'][:14]:<15} {r['city_code']:<15} {r['host']:<32} {r['latency_ms']} ms"
        )
