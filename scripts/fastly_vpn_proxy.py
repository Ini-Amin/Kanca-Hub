#!/usr/bin/env python3
"""Fastly VPN Proxy Server for Mozilla IP Protection (50 GB Egress).

Provides a local HTTP forward proxy (default: 127.0.0.1:25347) that tunnels
traffic through Fastly Envoy nodes (port 2499) with Bearer token authentication,
automatic latency-based server selection, and transparent failover.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import ssl
import sys
import time
from pathlib import Path

# Local import
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fastly_serverlist

CONFIG_DIR = Path.home() / ".config" / "auto-freecf"
TOKEN_FILE = CONFIG_DIR / "fastly_vpn_token.txt"
DEFAULT_PORT = 25347

logging.basicConfig(
    level=logging.INFO,
    format="[fastly-proxy] %(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fastly-proxy")


class FastlyProxyServer:
    def __init__(
        self,
        listen_host: str = "127.0.0.1",
        listen_port: int = DEFAULT_PORT,
        country_filter: str | None = None,
        replenish_gb: float = 45.0,
    ):
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.country_filter = country_filter
        self.replenish_threshold = int(replenish_gb * 1024 * 1024 * 1024)

        self.bytes_transferred = 0
        self.replenishing = False
        self.ranked_nodes: list[dict] = []
        self.active_node_idx = 0
        self.token = ""
        self.token_mtime = 0.0

        self.ssl_ctx = ssl.create_default_context()

    async def trigger_replenish(self, reason: str = "quota"):
        """Auto-mint fresh 50 GB token via fxa_tempik_e2e in background."""
        if self.replenishing:
            return
        self.replenishing = True
        log.warning("Triggering auto-replenish for Fastly VPN (reason: %s)...", reason)
        try:
            venv_py = Path.home() / ".local/share/auto-freecf/venv/bin/python"
            e2e_script = Path(__file__).resolve().parent / "fxa_tempik_e2e.py"
            proc = await asyncio.create_subprocess_exec(
                str(venv_py), str(e2e_script),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode == 0:
                log.info("Auto-replenish succeeded! Reloading fresh 50 GB token...")
                self.get_token()
                self.bytes_transferred = 0
            else:
                log.error("Auto-replenish failed: %s", stderr.decode(errors="ignore")[:200])
        except Exception as e:
            log.error("Auto-replenish error: %s", e)
        finally:
            self.replenishing = False

    def get_token(self) -> str:
        """Read token from environment or disk, reloading if modified."""
        env_tok = os.environ.get("FASTLY_VPN_TOKEN", "").strip()
        if env_tok:
            return env_tok
        if TOKEN_FILE.exists():
            try:
                mtime = TOKEN_FILE.stat().st_mtime
                if mtime > self.token_mtime:
                    self.token = TOKEN_FILE.read_text().strip()
                    self.token_mtime = mtime
            except Exception as e:
                log.warning("Failed reading token file: %s", e)
        return self.token

    def refresh_nodes(self):
        """Rank candidate nodes by latency and select the fastest."""
        log.info("Probing Fastly VPN nodes (country=%s)...", self.country_filter or "ALL")
        self.ranked_nodes = fastly_serverlist.rank_servers(
            country_filter=self.country_filter, max_candidates=15
        )
        self.active_node_idx = 0
        if self.ranked_nodes:
            top = self.ranked_nodes[0]
            log.info(
                "Selected fastest node: %s (%s, %s ms)",
                top["host"],
                top["country_name"],
                top["latency_ms"],
            )
        else:
            log.error("No healthy Fastly VPN nodes found!")

    def current_node(self) -> dict | None:
        if not self.ranked_nodes:
            self.refresh_nodes()
        if not self.ranked_nodes:
            return None
        return self.ranked_nodes[self.active_node_idx % len(self.ranked_nodes)]

    def rotate_node(self):
        """Switch to the next node in the pool."""
        if self.ranked_nodes:
            self.active_node_idx = (self.active_node_idx + 1) % len(self.ranked_nodes)
            nxt = self.current_node()
            if nxt:
                log.warning(
                    "Rotated to next node: %s (%s, %s ms)",
                    nxt["host"],
                    nxt["country_name"],
                    nxt["latency_ms"],
                )

    async def handle_client(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ):
        try:
            line = await client_reader.readline()
            if not line:
                client_writer.close()
                return

            parts = line.decode(errors="ignore").strip().split()
            if len(parts) < 2:
                client_writer.close()
                return

            method, target = parts[0].upper(), parts[1]

            # Drain remainder of client request headers
            while True:
                h_line = await client_reader.readline()
                if not h_line or h_line == b"\r\n":
                    break

            if method == "CONNECT":
                await self.handle_connect(client_reader, client_writer, target)
            else:
                # HTTP GET / health info
                body = (
                    f"Fastly VPN Proxy (Active Node: {self.current_node()})\n"
                ).encode()
                resp = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/plain\r\n"
                    b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
                )
                client_writer.write(resp)
                await client_writer.drain()
                client_writer.close()
        except Exception as e:
            log.debug("Client handler error: %s", e)
            try:
                client_writer.close()
            except Exception:
                pass

    async def handle_connect(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        target: str,
    ):
        token = self.get_token()
        max_attempts = 2

        for attempt in range(max_attempts):
            node = self.current_node()
            if not node:
                client_writer.write(b"HTTP/1.1 502 Bad Gateway (No Nodes)\r\n\r\n")
                await client_writer.drain()
                client_writer.close()
                return

            host, port = node["host"], node["port"]
            try:
                upstream_reader, upstream_writer = await asyncio.open_connection(
                    host, port, ssl=self.ssl_ctx, server_hostname=host
                )

                # Send CONNECT request to Fastly Envoy with Bearer Token
                headers = [
                    f"CONNECT {target} HTTP/1.1",
                    f"Host: {target}",
                    f"User-Agent: Mozilla/5.0 (rv:158.0) Gecko/20100101 Firefox/158.0",
                ]
                if token:
                    headers.append(f"Proxy-Authorization: Bearer {token}")
                req_bytes = "\r\n".join(headers).encode() + b"\r\n\r\n"
                upstream_writer.write(req_bytes)
                await upstream_writer.drain()

                # Read response from Fastly
                resp_line = await upstream_reader.readline()
                if not resp_line:
                    raise ConnectionResetError("Fastly node closed connection early")

                resp_str = resp_line.decode(errors="ignore")
                if "200" not in resp_str:
                    # Drain headers to log error
                    err_buf = [resp_str.strip()]
                    while True:
                        l = await upstream_reader.readline()
                        if not l or l == b"\r\n":
                            break
                        err_buf.append(l.decode(errors="ignore").strip())

                    err_msg = " | ".join(err_buf[:3])
                    log.warning("Fastly CONNECT rejected: %s", err_msg)
                    if any(k in err_msg for k in ["JWT", "auth-failed", "401", "407", "429"]):
                        asyncio.create_task(self.trigger_replenish(reason=err_msg[:40]))
                    upstream_writer.close()
                    self.rotate_node()
                    continue

                # Drain Fastly 200 headers
                while True:
                    l = await upstream_reader.readline()
                    if not l or l == b"\r\n":
                        break

                # Send 200 Connection Established to client
                client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await client_writer.drain()

                # Bidirectional pipe
                await self.pipe_bidirectional(
                    client_reader, client_writer, upstream_reader, upstream_writer
                )
                return
            except Exception as e:
                log.warning("Failed through node %s: %s", host, e)
                self.rotate_node()

        # If all attempts failed
        try:
            client_writer.write(b"HTTP/1.1 504 Gateway Timeout\r\n\r\n")
            await client_writer.drain()
            client_writer.close()
        except Exception:
            pass

    async def pipe_bidirectional(
        self,
        c_r: asyncio.StreamReader,
        c_w: asyncio.StreamWriter,
        u_r: asyncio.StreamReader,
        u_w: asyncio.StreamWriter,
    ):
        async def _forward(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            try:
                while True:
                    buf = await reader.read(65536)
                    if not buf:
                        break
                    self.bytes_transferred += len(buf)
                    if self.bytes_transferred >= self.replenish_threshold and not self.replenishing:
                        asyncio.create_task(self.trigger_replenish(reason="threshold_exceeded"))
                    writer.write(buf)
                    await writer.drain()
            except Exception:
                pass
            finally:
                try:
                    writer.close()
                except Exception:
                    pass

        await asyncio.gather(
            asyncio.create_task(_forward(c_r, u_w)),
            asyncio.create_task(_forward(u_r, c_w)),
            return_exceptions=True,
        )

    async def run(self):
        self.refresh_nodes()
        server = await asyncio.start_server(
            self.handle_client, self.listen_host, self.listen_port
        )
        log.info(
            "Fastly VPN Proxy listening on http://%s:%d",
            self.listen_host,
            self.listen_port,
        )
        async with server:
            await server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fastly VPN Proxy Client")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port to listen on")
    parser.add_argument("--country", type=str, default="", help="Country filter (e.g. SG, JP, US)")
    args = parser.parse_args()

    proxy = FastlyProxyServer(
        listen_port=args.port, country_filter=args.country or None
    )
    try:
        asyncio.run(proxy.run())
    except KeyboardInterrupt:
        log.info("Proxy stopped.")
