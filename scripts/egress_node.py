#!/usr/bin/env python3
"""
egress_node.py — turn THIS device's own internet connection into a proxy node.

Idea (user): any device on the same network (wifi/lAN/Tailscale) can act as an
egress for the others — e.g. a phone on mobile data becomes a "mobile" egress for
the laptop, or a host on a different ISP/region becomes a clean hop. Instead of
harvesting public proxies, you reuse real devices you already own.

Two roles:

  SERVE  — run this device as an egress node: a minimal HTTP/HTTPS CONNECT proxy
           that forwards traffic through THIS device's normal connection (its own
           wifi/carrier IP). Bind to a LAN or Tailscale interface so siblings can
           reach it. No proxy pool required.

      python3 scripts/egress_node.py serve --host 0.0.0.0 --port 8899
      # then, from any other device:  curl -x http://<this-ip>:8899 https://api.ipify.org

  REGISTER — remember a remote node URL so the egress ladder can try it as a hop.

      python3 scripts/egress_node.py add phone http://100.77.106.64:8899
      python3 scripts/egress_node.py list
      python3 scripts/egress_node.py remove phone

Nodes are stored at ~/.config/auto-freecf/egress_nodes.json and consumed by
scripts/egress.py (auto ladder) and kancahub.

Safety: the SERVE proxy is UNAUTHENTICATED — only bind it to a trusted network
(Tailscale tailnet / your own LAN). It is not meant to be exposed to the internet.
"""

from __future__ import annotations

import argparse
import json
import os
import select
import socket
import socketserver
import sys
import time
import urllib.request
from pathlib import Path

NODES_PATH = Path(os.environ.get("EGRESS_NODES", Path.home() / ".config" / "auto-freecf" / "egress_nodes.json"))

# --------------------------------------------------------------------------- registry


def load_nodes(path: Path | str | None = None) -> dict[str, str]:
    p = Path(path) if path else NODES_PATH
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def save_nodes(nodes: dict[str, str], path: Path | str | None = None) -> None:
    p = Path(path) if path else NODES_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(nodes, indent=2, ensure_ascii=False) + "\n")


def add_node(name: str, url: str, path: Path | str | None = None) -> dict[str, str]:
    nodes = load_nodes(path)
    if "://" not in url:
        url = "http://" + url
    nodes[name] = url.rstrip("/")
    save_nodes(nodes, path)
    return nodes


def remove_node(name: str, path: Path | str | None = None) -> dict[str, str]:
    nodes = load_nodes(path)
    nodes.pop(name, None)
    save_nodes(nodes, path)
    return nodes


def probe_node(url: str, timeout: float = 6.0) -> str | None:
    """Return the node's exit IP if it is reachable, else None."""
    try:
        r = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": url, "https": url})
        ).open("https://api.ipify.org", timeout=timeout)
        ip = r.read().decode().strip()
        return ip or None
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- serve


class _EgressHandler(socketserver.BaseRequestHandler):
    """Minimal forward proxy: CONNECT tunnel (HTTPS) + absolute-URI (HTTP).

    Forwards through THIS device's own network stack (no upstream proxy), so the
    exit IP is this device's real IP.
    """

    def _connect(self, host: str, port: int) -> socket.socket:
        return socket.create_connection((host, port), timeout=15)

    def _pump(self, a: socket.socket, b: socket.socket) -> None:
        socks = [a, b]
        try:
            while True:
                r, _, x = select.select(socks, [], socks, 30)
                if x:
                    break
                for s in r:
                    data = s.recv(65536)
                    if not data:
                        return
                    (b if s is a else a).sendall(data)
        except Exception:  # noqa: BLE001
            return

    def handle(self) -> None:
        try:
            self.request.settimeout(20)
            raw = self.request.recv(65536)
            if not raw:
                return
            head, _, rest = raw.partition(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            method, target, _ver = (lines[0].split(" ") + ["", ""])[:3]

            if method.upper() == "CONNECT":
                host, _, port = target.partition(":")
                upstream = self._connect(host, int(port or 443))
                self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                if rest:
                    upstream.sendall(rest)
                self._pump(self.request, upstream)
                upstream.close()
                return

            # plain HTTP absolute-URI forward
            if target.startswith("http://"):
                without = target[7:]
                host, _, path = without.partition("/")
                host_only, _, port = host.partition(":")
                upstream = self._connect(host_only, int(port or 80))
                rebuilt = [lines[0].replace(target, "/" + path)] + lines[1:]
                upstream.sendall(("\r\n".join(rebuilt)).encode("latin-1") + b"\r\n\r\n" + rest)
                self._pump(self.request, upstream)
                upstream.close()
                return

            self.request.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\n")
        except Exception:  # noqa: BLE001
            try:
                self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            except Exception:  # noqa: BLE001
                pass


class _EgressServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def serve(host: str = "0.0.0.0", port: int = 8899) -> int:
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"  ⚠ binding {host}: UNAUTHENTICATED proxy — only on a trusted "
              f"network (Tailscale tailnet / your LAN).", file=sys.stderr)
    srv = _EgressServer((host, port), _EgressHandler)
    try:
        me = urllib.request.urlopen("https://api.ipify.org", timeout=6).read().decode().strip()
    except Exception:  # noqa: BLE001
        me = "?"
    print(f"✓ egress node serving on http://{host}:{port}  (this device's exit IP: {me})")
    print(f"  siblings can use:  --proxy http://<this-device-ip>:{port}")
    print("  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  node stopped.")
    finally:
        srv.server_close()
    return 0


# --------------------------------------------------------------------------- cli


def main() -> int:
    ap = argparse.ArgumentParser(description="Use a device's own connection as a proxy node")
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("serve", help="run THIS device as an egress node")
    s.add_argument("--host", default="0.0.0.0", help="bind interface (default 0.0.0.0; use a Tailscale IP for safety)")
    s.add_argument("--port", type=int, default=8899)

    a = sub.add_parser("add", help="remember a remote egress node")
    a.add_argument("name")
    a.add_argument("url", help="e.g. http://100.77.106.64:8899")

    r = sub.add_parser("remove", help="forget a node")
    r.add_argument("name")

    sub.add_parser("list", help="list nodes with a live probe")

    args = ap.parse_args()

    if args.cmd == "serve":
        return serve(args.host, args.port)
    if args.cmd == "add":
        add_node(args.name, args.url)
        print(f"  ✓ registered node '{args.name}' -> {args.url}")
        return 0
    if args.cmd == "remove":
        remove_node(args.name)
        print(f"  ✓ removed node '{args.name}'")
        return 0

    # default: list (with probe)
    nodes = load_nodes()
    if not nodes:
        print("  no egress nodes registered. Add one:  egress_node.py add phone http://<ip>:8899")
        return 0
    print(f"  {'name':<12} {'url':<34} {'exit IP':<16} status")
    for name, url in nodes.items():
        ip = probe_node(url, timeout=5.0)
        print(f"  {name:<12} {url:<34} {ip or '-':<16} {'✅ up' if ip else '❌ down'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
