#!/usr/bin/env python3
"""
Residential Proxy Gateway (DEPRECATED -> see scripts/proxy_gateway.py).

NOTE: This custom gateway has been superseded by scripts/proxy_gateway.py, which
is backed by PetaniProxy's battle-tested LocalProxyBridge and supports HTTP,
HTTPS, SOCKS4, and SOCKS5 upstreams with connection rotation and X-Session-ID
sticky sessions.

This file is maintained as a backward-compatible shim.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Add scripts directory to path to import proxy_gateway
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    from proxy_gateway import (
        LocalProxyBridge,
        ProxyGatewayHandler,
        ProxyGatewayServer,
        load_pool_file,
        run_gateway,
    )
except ImportError:
    run_gateway = None

# Re-export original classes for backward compatibility
from urllib.parse import urlparse
import base64
import itertools

HOST = "127.0.0.1"


class Upstream:
    def __init__(self, url: str):
        if "://" not in url:
            if url.count(":") == 3:
                host, port, user, pw = url.split(":", 3)
                url = f"http://{user}:{pw}@{host}:{port}"
            else:
                url = "http://" + url
        u = urlparse(url)
        self.scheme = (u.scheme or "http").lower()
        self.host = u.hostname or ""
        self.port = u.port or 8080
        self.username = u.username or ""
        self.password = u.password or ""
        self.raw = url

    @property
    def auth_header(self) -> str | None:
        if not self.username:
            return None
        token = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
        return f"Proxy-Authorization: Basic {token}"

    def __repr__(self) -> str:
        u = f"{self.username}:***@" if self.username else ""
        return f"{self.scheme}://{u}{self.host}:{self.port}"


class Rotator:
    def __init__(self, upstreams: list[Upstream]):
        self._upstreams = upstreams
        self._cycle = itertools.cycle(range(len(upstreams)))
        self._lock = asyncio.Lock()

    async def next(self) -> Upstream:
        async with self._lock:
            return self._upstreams[next(self._cycle)]

    def __len__(self) -> int:
        return len(self._upstreams)


def load_pool(path: str) -> list[Upstream]:
    p = Path(path)
    if not p.exists():
        print(f"✗ pool file not found: {path}", file=sys.stderr)
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            out.append(Upstream(line))
        except Exception:
            pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Residential proxy gateway (superseded by scripts/proxy_gateway.py)"
    )
    ap.add_argument("--pool", required=True, help="file with proxy URLs (one per line)")
    ap.add_argument("--port", type=int, default=8899, help="local port (default: 8899)")
    args = ap.parse_args()

    print("[!] Notice: residential_gateway.py is deprecated; delegating to proxy_gateway.py (PetaniProxy bridge)")
    if run_gateway is not None:
        return run_gateway(pool_path=args.pool, port=args.port)

    print("✗ proxy_gateway.py could not be loaded", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
