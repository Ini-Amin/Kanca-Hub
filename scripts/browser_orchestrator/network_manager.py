"""browser_orchestrator.network_manager — Proxy & Network Egress Manager.

Resolves, verifies, and formats network egress proxies across all browser adapters.
"""
from __future__ import annotations

import os
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class ProxyConfig:
    server: str
    username: Optional[str] = None
    password: Optional[str] = None
    raw_url: Optional[str] = None

    def to_playwright_dict(self) -> dict[str, str]:
        d = {"server": self.server}
        if self.username:
            d["username"] = self.username
        if self.password:
            d["password"] = self.password
        return d

    def to_chrome_arg(self) -> str:
        # Chrome flag only takes host:port; auth requires extension or CDP
        parsed = urllib.parse.urlparse(self.server)
        host = parsed.netloc or parsed.path
        return f"--proxy-server={host}"


class ProxyNetworkManager:
    """Manages egress routing and proxy formatting for browser sessions."""

    @classmethod
    def resolve_proxy(cls, proxy_str: Optional[str] = None, country: Optional[str] = None) -> Optional[ProxyConfig]:
        candidate = proxy_str or os.environ.get("FORGE_PROXY") or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        
        # If no explicit proxy, check local proxies.txt
        if not candidate:
            for p in (Path("proxies.txt"), Path.home() / ".config/forge/proxies.txt"):
                if p.is_file():
                    for line in p.read_text().splitlines():
                        line = line.strip()
                        if line and not line.startswith("#"):
                            candidate = line
                            break
                    if candidate:
                        break

        if not candidate:
            return None

        # Parse candidate URL
        raw = candidate.strip()
        if "://" not in raw:
            raw = f"http://{raw}"

        parsed = urllib.parse.urlparse(raw)
        server = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}" if parsed.port else f"{parsed.scheme}://{parsed.hostname}"
        
        return ProxyConfig(
            server=server,
            username=parsed.username,
            password=parsed.password,
            raw_url=raw,
        )
