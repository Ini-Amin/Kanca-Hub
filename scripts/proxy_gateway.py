#!/usr/bin/env python3
"""
PetaniProxy-backed local proxy gateway for residential & authenticated proxies.

Provides an authless localhost HTTP proxy endpoint (default: http://127.0.0.1:8899)
that accepts connections from Chrome or HTTP clients and forwards them through a
pool of upstream authenticated proxies (HTTP, HTTPS, SOCKS4, SOCKS5).

Features:
  - Backed by PetaniProxy's LocalProxyBridge (handling HTTP & SOCKS handshakes)
  - Round-robin per-connection rotation
  - Sticky session support: sending 'X-Session-ID: <id>' pins one upstream for 600s
  - Direct compatibility with Chrome's --proxy-server=http://127.0.0.1:8899

Usage:
    python3 scripts/proxy_gateway.py --pool signup_from_scratch/proxies.txt
    python3 scripts/proxy_gateway.py --pool proxies.txt --port 8899 --scheme auto
"""

from __future__ import annotations

import argparse
import base64
import os
import select
import socket
import socketserver
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Optional

PETANI_DIR = Path.home() / "petani-proxy"
if str(PETANI_DIR) not in sys.path and PETANI_DIR.exists():
    sys.path.insert(0, str(PETANI_DIR))

try:
    from core.proxy_bridge import (
        LocalProxyBridge,
        ProxyBridgeError,
        parse_proxy_url,
        prepare_chromium_proxy,
        prepare_http_compatible_proxy,
        proxy_has_auth,
        _recv_until_headers,
        _relay,
        _rewrite_http_request,
        _split_host_port,
    )
    HAVE_PETANI_BRIDGE = True
except ImportError:
    HAVE_PETANI_BRIDGE = False

    class ProxyBridgeError(OSError):
        def __init__(self, kind, message):
            self.kind = str(kind or "bridge")
            super().__init__(f"{self.kind}: {message}")

    def parse_proxy_url(proxy):
        raw = str(proxy or "").strip()
        if not raw:
            return None
        if "://" not in raw:
            raw = "http://" + raw
        try:
            return urllib.parse.urlsplit(raw)
        except Exception:
            return None

    def proxy_has_auth(proxy):
        p = parse_proxy_url(proxy)
        return bool(p and p.hostname and (p.username is not None or p.password is not None))

    def prepare_chromium_proxy(proxy, log=None):
        return str(proxy or ""), None

    def prepare_http_compatible_proxy(proxy, log=None):
        return str(proxy or ""), None

    def _recv_until_headers(sock, timeout=20, limit=65536):
        sock.settimeout(timeout)
        data = b""
        while b"\r\n\r\n" not in data and len(data) < limit:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        return data

    def _relay(left, right, timeout=90):
        left.settimeout(timeout)
        right.settimeout(timeout)
        sockets = [left, right]
        while True:
            readable, _, _ = select.select(sockets, [], [], timeout)
            if not readable:
                return
            for s in readable:
                d = s.recv(65536)
                if not d:
                    return
                (right if s is left else left).sendall(d)

    def _split_host_port(value, default_port):
        text = str(value or "").strip()
        if ":" in text:
            h, p = text.rsplit(":", 1)
            try:
                return h, int(p)
            except ValueError:
                pass
        return text, default_port

    def _rewrite_http_request(initial):
        head, body = initial.split(b"\r\n\r\n", 1)
        lines = head.split(b"\r\n")
        first = lines[0].decode("latin1", "ignore")
        parts = first.split(" ", 2)
        if len(parts) != 3:
            raise ProxyBridgeError("request", "invalid HTTP request line")
        method, target, version = parts
        parsed = urllib.parse.urlsplit(target)
        if parsed.scheme in ("http", "https") and parsed.hostname:
            host = parsed.hostname
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            path = urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            lines[0] = f"{method} {path} {version}".encode("latin1")
        else:
            host_header = ""
            for line in lines[1:]:
                if line.lower().startswith(b"host:"):
                    host_header = line.split(b":", 1)[1].strip().decode("latin1", "ignore")
                    break
            host, port = _split_host_port(host_header, 80)
        filtered = [l for l in lines if not l.lower().startswith(b"proxy-authorization:")]
        return host, port, b"\r\n".join(filtered) + b"\r\n\r\n" + body

    class LocalProxyBridge:
        def __init__(self, proxy_url):
            p = parse_proxy_url(proxy_url)
            if not p or not p.hostname:
                raise ValueError("invalid proxy URL")
            self.upstream_scheme = (p.scheme or "http").lower()
            self.upstream_host = p.hostname
            self.upstream_port = p.port or 8080
            self.username = urllib.parse.unquote(p.username or "")
            self.password = urllib.parse.unquote(p.password or "")
            raw_auth = f"{self.username}:{self.password}".encode("utf-8")
            self.auth_header = base64.b64encode(raw_auth).decode("ascii") if (self.username or self.password) else ""
            self.timeout = 20
            self.relay_timeout = 90
            self.is_http_upstream = self.upstream_scheme in ("http", "https")

        def open_proxy_socket(self):
            return socket.create_connection((self.upstream_host, self.upstream_port), timeout=self.timeout)

        def inject_proxy_auth(self, data):
            if not self.auth_header or b"\r\n\r\n" not in data:
                return data
            if b"\r\nproxy-authorization:" in data.lower():
                return data
            head, body = data.split(b"\r\n\r\n", 1)
            auth_line = f"Proxy-Authorization: Basic {self.auth_header}".encode("latin1")
            return head + b"\r\n" + auth_line + b"\r\n\r\n" + body

        def open_socks_target(self, host, port):
            raise ProxyBridgeError("socks", "standalone fallback only supports HTTP upstreams")


__all__ = [
    "LocalProxyBridge",
    "prepare_chromium_proxy",
    "prepare_http_compatible_proxy",
    "proxy_has_auth",
    "parse_proxy_url",
    "ProxyGatewayServer",
    "ProxyGatewayHandler",
    "normalize_proxy_line",
    "load_pool_file",
]


def extract_session_id(header_bytes: bytes) -> Optional[str]:
    """Extract X-Session-ID or X-Sticky-Session from initial HTTP headers."""
    try:
        head = header_bytes.split(b"\r\n\r\n", 1)[0]
        for line in head.split(b"\r\n"):
            if b":" in line:
                k, v = line.split(b":", 1)
                clean_k = k.strip().lower()
                if clean_k in (b"x-session-id", b"x-sticky-session"):
                    val = v.strip().decode("latin1", "ignore")
                    if val:
                        return val
    except Exception:
        pass
    return None


def strip_internal_headers(header_bytes: bytes) -> bytes:
    """Remove internal X-Session-ID / X-Sticky-Session headers before forwarding."""
    try:
        head, sep, rest = header_bytes.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        filtered = [
            l for l in lines
            if not l.lower().startswith((b"x-session-id:", b"x-sticky-session:"))
        ]
        return b"\r\n".join(filtered) + sep + rest
    except Exception:
        return header_bytes


def normalize_proxy_line(line: str, default_scheme: str = "auto") -> Optional[str]:
    """Normalise proxy string (handles host:port:user:pass and scheme prefixes)."""
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None

    scheme = "http" if default_scheme == "auto" else default_scheme

    # host:port:user:pass
    if "://" not in raw:
        if raw.count(":") == 3:
            host, port, user, pw = raw.split(":", 3)
            return f"{scheme}://{user}:{pw}@{host}:{port}"
        return f"{scheme}://" + raw

    # Has scheme; if user explicitly asked for a specific scheme (not auto), override it
    if default_scheme != "auto":
        u = urllib.parse.urlsplit(raw)
        return urllib.parse.urlunsplit((default_scheme, u.netloc, u.path, u.query, u.fragment))

    return raw


def load_pool_file(path: str, scheme: str = "auto") -> list[str]:
    """Load proxy URLs from file, filtering empty and commented lines."""
    p = Path(path)
    if not p.exists():
        print(f"✗ pool file not found: {path}", file=sys.stderr)
        return []
    proxies = []
    for line in p.read_text(encoding="utf-8").splitlines():
        norm = normalize_proxy_line(line, default_scheme=scheme)
        if norm:
            proxies.append(norm)
    return proxies


class ProxyGatewayHandler(socketserver.BaseRequestHandler):
    """Processes incoming HTTP/HTTPS proxy requests and forwards them through the bridge."""

    def handle(self):
        server: ProxyGatewayServer = self.server  # type: ignore
        upstream = None
        try:
            initial = _recv_until_headers(self.request, timeout=20)
            if not initial:
                return

            session_id = extract_session_id(initial)
            bridge = server.acquire_bridge(session_id)

            first_line = initial.split(b"\r\n", 1)[0].decode("latin1", "ignore")
            if first_line.upper().startswith("CONNECT "):
                target = first_line.split()[1]
                if bridge.is_http_upstream:
                    upstream = bridge.open_proxy_socket()
                    req = [f"CONNECT {target} HTTP/1.1", f"Host: {target}"]
                    if bridge.auth_header:
                        req.append(f"Proxy-Authorization: Basic {bridge.auth_header}")
                    upstream.sendall(("\r\n".join(req) + "\r\n\r\n").encode("latin1"))
                    response = _recv_until_headers(upstream, timeout=bridge.timeout)
                    status = response.split(b"\r\n", 1)[0] if response else b""
                    if b" 200 " not in status:
                        code = status.decode("latin1", "ignore")
                        kind = "http_proxy_auth" if b" 407 " in status else "http_connect"
                        raise ProxyBridgeError(kind, code or "proxy CONNECT failed")
                    self.request.sendall(response)
                else:
                    host, port = _split_host_port(target, 443)
                    upstream = bridge.open_socks_target(host, port)
                    self.request.sendall(b"HTTP/1.1 200 Connection Established\r\nProxy-Agent: local-bridge\r\n\r\n")
                _relay(self.request, upstream, timeout=bridge.relay_timeout)
                return

            # Plain HTTP request: strip internal tracking headers and inject auth
            clean_initial = strip_internal_headers(initial)
            if bridge.is_http_upstream:
                upstream = bridge.open_proxy_socket()
                upstream.sendall(bridge.inject_proxy_auth(clean_initial))
            else:
                host, port, request_data = _rewrite_http_request(clean_initial)
                upstream = bridge.open_socks_target(host, port)
                upstream.sendall(request_data)
            _relay(self.request, upstream, timeout=bridge.relay_timeout)
        except ProxyBridgeError:
            try:
                self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            except Exception:
                pass
        except Exception:
            try:
                self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            except Exception:
                pass
        finally:
            if upstream is not None:
                try:
                    upstream.close()
                except Exception:
                    pass


class ProxyGatewayServer(socketserver.ThreadingTCPServer):
    """Multi-threaded local HTTP proxy server with round-robin and sticky-session routing."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        pool: list[str],
        session_ttl: float = 600.0,
    ):
        super().__init__(server_address, ProxyGatewayHandler)
        self.pool = pool
        self.session_ttl = session_ttl
        self._index = 0
        self._lock = threading.Lock()
        self._sessions: dict[str, tuple[str, float]] = {}  # session_id -> (proxy_url, expiry)
        self._bridge_cache: dict[str, LocalProxyBridge] = {}

    def acquire_bridge(self, session_id: Optional[str] = None) -> LocalProxyBridge:
        """Acquire a LocalProxyBridge for this connection (sticky if session_id provided)."""
        with self._lock:
            now = time.time()
            proxy_url = None

            # 1. Check pinned session
            if session_id:
                clean_sid = str(session_id).strip()
                if clean_sid in self._sessions:
                    p, expiry = self._sessions[clean_sid]
                    if now < expiry:
                        self._sessions[clean_sid] = (p, now + self.session_ttl)
                        proxy_url = p
                    else:
                        del self._sessions[clean_sid]

            # 2. Pick next proxy via round-robin
            if not proxy_url:
                proxy_url = self.pool[self._index % len(self.pool)]
                self._index += 1
                if session_id:
                    self._sessions[str(session_id).strip()] = (proxy_url, now + self.session_ttl)

            # 3. Retrieve or create bridge
            if proxy_url not in self._bridge_cache:
                self._bridge_cache[proxy_url] = LocalProxyBridge(proxy_url)

            return self._bridge_cache[proxy_url]


def run_gateway(
    pool_path: str,
    port: int = 8899,
    host: str = "127.0.0.1",
    scheme: str = "auto",
    ttl: float = 600.0,
) -> int:
    """Run the proxy gateway until interrupted."""
    pool = load_pool_file(pool_path, scheme=scheme)
    if not pool:
        print(f"✗ no usable proxies found in {pool_path}", file=sys.stderr)
        return 1

    server = None
    # Try the requested port; if busy, auto-pick a free one so the wizard never
    # dies with 'Address already in use' (a stale gateway from a previous run).
    import socket as _socket
    for attempt_port in [port] + list(_free_ports(port, 5)):
        try:
            server = ProxyGatewayServer((host, attempt_port), pool=pool, session_ttl=ttl)
            port = attempt_port
            break
        except OSError as e:
            if e.errno == 98:  # Address already in use
                print(f"  (port {attempt_port} busy — trying another)", file=sys.stderr)
                continue
            raise
    if server is None:
        print(f"✗ could not bind a port near {port}", file=sys.stderr)
        return 1

    backend_name = "PetaniProxy bridge" if HAVE_PETANI_BRIDGE else "standalone fallback"
    print(f"✓ proxy gateway on http://{host}:{port} ({backend_name})", flush=True)
    print(f"  upstreams: {len(pool)} (rotating per connection, 600s sticky via X-Session-ID)", flush=True)
    for p in pool[:3]:
        # mask password for clean display
        parsed = parse_proxy_url(p)
        if parsed and parsed.username:
            masked = f"{parsed.scheme}://{parsed.username}:***@{parsed.hostname}:{parsed.port}"
        else:
            masked = p
        print(f"    - {masked}", flush=True)
    if len(pool) > 3:
        print(f"    ... and {len(pool) - 3} more", flush=True)
    print(f"  point Chrome at it:  --proxy-server=http://{host}:{port}\n", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nGateway stopped.", flush=True)
    finally:
        server.server_close()
    return 0


def _free_ports(start: int, count: int):
    """Yield up to `count` likely-free ports starting near `start`."""
    import socket as _socket
    found = 0
    p = start
    while found < count:
        p += 1
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        try:
            s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", p))
            s.close()
            found += 1
            yield p
        except OSError:
            s.close()
            continue


def main():
    ap = argparse.ArgumentParser(description="PetaniProxy local rotating proxy gateway with auth injection")
    ap.add_argument("--pool", required=True, help="file with proxy URLs (one per line)")
    ap.add_argument("--port", type=int, default=8899, help="local port to listen on (default: 8899)")
    ap.add_argument("--host", default="127.0.0.1", help="local interface to bind (default: 127.0.0.1)")
    ap.add_argument("--scheme", choices=["auto", "http", "socks5", "socks4", "https"], default="auto",
                    help="upstream scheme (default: auto)")
    ap.add_argument("--ttl", type=float, default=600.0, help="sticky session TTL in seconds (default: 600)")
    args = ap.parse_args()

    sys.exit(run_gateway(
        pool_path=args.pool,
        port=args.port,
        host=args.host,
        scheme=args.scheme,
        ttl=args.ttl,
    ))


if __name__ == "__main__":
    main()
