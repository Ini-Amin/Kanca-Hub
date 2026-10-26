#!/usr/bin/env python3
"""zcode_egress_rotator.py — Auto-Rotating Residential Proxy Relay for ZCode Egress.

Listens on localhost (default: 127.0.0.1:25346) and accepts plaintext CONNECT
requests from zcode_bridge_server.py. Forwards each connection through a
dynamically rotating pool of residential proxies (UrbanVPN / VPN fleet) across
multiple countries (SG, JP, KR, US, DE, GB).

Bypasses Alibaba WAF 3012 by preventing any single egress IP from being pinned.
"""

from __future__ import annotations

import argparse
import base64
import logging
import os
import select
import socket
import ssl
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

# Repo root import
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.urban_vpn as uv

DEFAULT_PORT = 25346
POOL_COUNTRIES = ["SG", "JP", "KR", "US", "DE", "GB", "NL"]
REFRESH_INTERVAL = 900  # Refresh proxy pool every 15 minutes

logging.basicConfig(
    level=logging.INFO,
    format="[egress-rotator] %(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("egress-rotator")


class ProxyPool:
    def __init__(self, countries: list[str] = POOL_COUNTRIES):
        self.countries = countries
        self.proxies: list[dict] = []
        self._index = 0
        self._lock = threading.Lock()
        self.refresh()

    def refresh(self):
        new_pool = []
        for c in self.countries:
            try:
                p = uv.get_proxy(country=c)
                if p and p.get("host") and p.get("port"):
                    new_pool.append(p)
                    log.info("Loaded residential proxy [%s]: %s:%s", c, p["host"], p["port"])
            except Exception as e:
                log.warning("Failed to fetch proxy for %s: %s", c, e)

        with self._lock:
            if new_pool:
                self.proxies = new_pool
                log.info("Proxy pool refreshed: %d live proxies available", len(self.proxies))
            else:
                log.warning("Refresh produced no proxies; keeping previous pool (%d)", len(self.proxies))

    def get_next(self) -> dict | None:
        with self._lock:
            if not self.proxies:
                return None
            p = self.proxies[self._index % len(self.proxies)]
            self._index += 1
            return p


def pipe_sockets(s1: socket.socket, s2: socket.socket):
    """Bidirectional socket relay with select."""
    try:
        while True:
            r, _, _ = select.select([s1, s2], [], [], 60)
            if not r:
                break
            for s in r:
                other = s2 if s is s1 else s1
                data = s.recv(8192)
                if not data:
                    return
                other.sendall(data)
    except Exception:
        pass
    finally:
        try:
            s1.close()
        except Exception:
            pass
        try:
            s2.close()
        except Exception:
            pass


def handle_client(client: socket.socket, pool: ProxyPool):
    remote = None
    try:
        client.settimeout(15)
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = client.recv(4096)
            if not chunk:
                client.close()
                return
            head += chunk
            if len(head) > 16384:
                client.close()
                return

        line = head.split(b"\r\n", 1)[0].decode("latin1")
        parts = line.split()
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            client.sendall(b"HTTP/1.1 405 Method Not Allowed\r\n\r\n")
            client.close()
            return

        target = parts[1]
        log.info("New CONNECT request to %s", target)
        t_host = target.split(":")[0]
        t_port = int(target.split(":")[1]) if ":" in target else 443

        # Try up to 3 candidate proxies
        connected = False
        for attempt in range(3):
            proxy = pool.get_next()
            if not proxy:
                break

            up_host = proxy["host"]
            up_port = int(proxy["port"])
            up_user = proxy.get("username", "")
            up_pass = proxy.get("password", "")
            is_ssl = proxy.get("proxy", "").startswith("https://")

            try:
                raw = socket.create_connection((up_host, up_port), timeout=10)
                if is_ssl:
                    ctx = ssl.create_default_context()
                    remote = ctx.wrap_socket(raw, server_hostname=up_host)
                else:
                    remote = raw

                req = f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n"
                if up_user:
                    creds = base64.b64encode(f"{up_user}:{up_pass}".encode()).decode()
                    req += f"Proxy-Authorization: Basic {creds}\r\n"
                req += "\r\n"

                remote.sendall(req.encode())
                remote.settimeout(10)
                resp = b""
                while b"\r\n\r\n" not in resp:
                    d = remote.recv(1024)
                    if not d:
                        break
                    resp += d

                if resp.startswith(b"HTTP/1.1 200") or resp.startswith(b"HTTP/1.0 200"):
                    connected = True
                    log.info("Handshake SUCCESS via %s:%d -> %s", up_host, up_port, target)
                    break
                else:
                    log.warning("Proxy %s returned non-200: %s", up_host, resp[:60])
                    remote.close()
                    remote = None
            except Exception as e:
                log.warning("Attempt %d via %s failed: %s", attempt + 1, up_host, e)
                if remote:
                    try:
                        remote.close()
                    except Exception:
                        pass
                remote = None

        if not connected or not remote:
            client.sendall(b"HTTP/1.1 502 Bad Gateway (All upstream proxies failed)\r\n\r\n")
            client.close()
            return

        client.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        pipe_sockets(client, remote)

    except Exception as e:
        log.error("Relay handler exception: %s", e)
        try:
            client.close()
        except Exception:
            pass


def bg_refresh_loop(pool: ProxyPool, interval: int = REFRESH_INTERVAL):
    while True:
        time.sleep(interval)
        try:
            log.info("Periodic proxy pool refresh running...")
            pool.refresh()
        except Exception as e:
            log.error("Error in background refresh: %s", e)


def main():
    parser = argparse.ArgumentParser(description="ZCode Auto-Rotating Egress Proxy")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to bind (default: 25346)")
    parser.add_argument("--bind", default="127.0.0.1", help="Address to bind (default: 127.0.0.1)")
    args = parser.parse_args()

    log.info("Initializing residential proxy pool...")
    pool = ProxyPool()

    # Start background refresh thread
    t = threading.Thread(target=bg_refresh_loop, args=(pool,), daemon=True)
    t.start()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.bind, args.port))
    server.listen(128)

    log.info("ZCode Rotating Egress Relay listening on %s:%d", args.bind, args.port)

    while True:
        try:
            client, addr = server.accept()
            th = threading.Thread(target=handle_client, args=(client, pool), daemon=True)
            th.start()
        except KeyboardInterrupt:
            log.info("Stopping egress rotator...")
            break
        except Exception as e:
            log.error("Accept error: %s", e)


if __name__ == "__main__":
    main()
