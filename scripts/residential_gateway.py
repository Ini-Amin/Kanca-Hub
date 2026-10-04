#!/usr/bin/env python3
"""
Residential Proxy Gateway — authless local gateway for authenticated proxies.

Why this exists:
  Chrome's ``--proxy-server`` flag cannot carry proxy username/password, and
  headless Chrome extensions are unreliable. This gateway listens on
  ``127.0.0.1:<port>`` with NO auth, and forwards each connection to an
  upstream authenticated residential proxy (rotating per connection).

Usage:
    python3 scripts/residential_gateway.py --pool signup_from_scratch/proxies.txt
    python3 scripts/residential_gateway.py --pool ... --port 8899
    # then point the pipeline at it:
    #   kancahub stack run -n 5 --proxy http://127.0.0.1:8899

It supports HTTP and HTTPS (CONNECT) with rotating upstream auth.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import itertools
import sys
from pathlib import Path
from urllib.parse import urlparse

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


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle_client(client_reader: asyncio.StreamReader,
                        client_writer: asyncio.StreamWriter,
                        rotator: Rotator) -> None:
    try:
        upstream = await rotator.next()
    except Exception:
        client_writer.close()
        return

    try:
        header_block = b""
        while b"\r\n\r\n" not in header_block:
            chunk = await client_reader.read(65536)
            if not chunk:
                client_writer.close()
                return
            header_block += chunk

        head, _, rest = header_block.partition(b"\r\n\r\n")
        lines = head.split(b"\r\n")
        request_line = lines[0].decode("latin-1", "replace")
        method, target, *_ = (request_line.split(" ") + ["", ""])[:3]

        if method.upper() == "CONNECT":
            # Tunnel: connect upstream, authenticate, then blindly pipe.
            up_reader, up_writer = await asyncio.open_connection(upstream.host, upstream.port)
            # Send CONNECT with auth.
            connect_req = f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n"
            if upstream.auth_header:
                connect_req += upstream.auth_header + "\r\n"
            connect_req += "\r\n"
            up_writer.write(connect_req.encode())
            await up_writer.drain()

            resp = await up_reader.readuntil(b"\r\n\r\n")
            if b" 200 " not in resp.split(b"\r\n")[0]:
                client_writer.write(resp)
                await client_writer.drain()
                client_writer.close()
                up_writer.close()
                return

            client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await client_writer.drain()
            await asyncio.gather(
                _pipe(client_reader, up_writer),
                _pipe(up_reader, client_writer),
            )
        else:
            # Plain HTTP: forward the request with Proxy-Authorization.
            up_reader, up_writer = await asyncio.open_connection(upstream.host, upstream.port)
            # Ensure target is absolute-form for the upstream proxy.
            new_lines = list(lines)
            new_lines[0] = f"{method} {target} HTTP/1.1".encode()
            # Strip any existing proxy-auth, then add ours.
            new_lines = [l for l in new_lines if not l.lower().startswith(b"proxy-authorization:")]
            payload = b"\r\n".join(new_lines) + b"\r\n"
            if upstream.auth_header:
                payload += upstream.auth_header.encode() + b"\r\n"
            payload += b"\r\n" + rest
            up_writer.write(payload)
            await up_writer.drain()
            await asyncio.gather(
                _pipe(up_reader, client_writer),
            )
            up_writer.close()
    except Exception:
        pass
    finally:
        try:
            client_writer.close()
        except Exception:
            pass


def load_pool(path: str) -> list[Upstream]:
    p = Path(path)
    if not p.exists():
        print(f"✗ pool file not found: {path}", file=sys.stderr)
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            out.append(Upstream(line))
        except Exception:
            pass
    return out


async def main_async(args) -> int:
    upstreams = load_pool(args.pool)
    if not upstreams:
        print("✗ no usable upstreams", file=sys.stderr)
        return 1

    rotator = Rotator(upstreams)
    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, rotator), HOST, args.port
    )
    print(f"✓ Residential gateway on http://{HOST}:{args.port}")
    print(f"  upstreams: {len(upstreams)} (rotating per connection)")
    for u in upstreams[:3]:
        print(f"    - {u}")
    print("  point Auto-FreeCF at it:  --proxy http://%s:%d" % (HOST, args.port))
    async with server:
        await server.serve_forever()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Authless gateway for authenticated residential proxies")
    ap.add_argument("--pool", required=True, help="file with proxy URLs (one per line)")
    ap.add_argument("--port", type=int, default=8899)
    args = ap.parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
