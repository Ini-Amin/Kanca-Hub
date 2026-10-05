"""
Camoufox helper utilities: proxy parsing, pool rotation, and gateway sticky sessions.
"""

from __future__ import annotations

import os
import sys
import urllib.parse
from pathlib import Path
from typing import Optional

# Ensure scripts dir is on sys.path for gateway_session
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    from gateway_session import apply_gateway_session, is_gateway
except ImportError:
    def is_gateway(proxy_url: Optional[str] = None) -> bool:
        return False

    async def apply_gateway_session(page_or_tab, session_id: Optional[str] = None) -> bool:
        return False


def to_camoufox_proxy(proxy_url_or_dict: str | dict | None) -> dict | None:
    """Convert proxy URL string or dict to Camoufox/Playwright proxy dictionary.

    Format:
        {"server": "http://host:port", "username": "...", "password": "..."}
    """
    if not proxy_url_or_dict:
        return None
    if isinstance(proxy_url_or_dict, dict):
        return proxy_url_or_dict

    raw = str(proxy_url_or_dict).strip()
    if not raw or raw.lower() == "none" or raw.lower() == "direct":
        return None

    # Handle host:port:user:pass
    if "://" not in raw and raw.count(":") == 3:
        host, port, user, pw = raw.split(":", 3)
        return {
            "server": f"http://{host}:{port}",
            "username": user,
            "password": pw,
        }

    if "://" not in raw:
        raw = "http://" + raw

    u = urllib.parse.urlsplit(raw)
    server = f"{u.scheme}://{u.hostname}"
    if u.port:
        server += f":{u.port}"

    d: dict[str, str] = {"server": server}
    if u.username:
        d["username"] = urllib.parse.unquote(u.username)
    if u.password:
        d["password"] = urllib.parse.unquote(u.password)
    return d


def load_proxy_pool(pool_path: str | Path | None) -> list[str]:
    """Load proxy URLs from a text file, ignoring comments and blanks."""
    if not pool_path:
        return []
    p = Path(pool_path)
    if not p.exists():
        return []
    lines = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def pick_proxy(
    proxy: str | None = None,
    pool_path: str | Path | None = None,
    index: int = 0,
) -> str | None:
    """Return a single proxy URL from explicit arg or pool file round-robin."""
    if proxy:
        return proxy
    pool = load_proxy_pool(pool_path)
    if pool:
        return pool[index % len(pool)]
    return None


async def setup_camoufox_gateway(
    context,
    proxy_url: str | None,
    session_id: str | None = None,
) -> bool:
    """If proxy_url points to a local gateway, attach X-Session-ID for sticky sessions."""
    if not proxy_url or not is_gateway(proxy_url):
        return False
    sid = session_id or os.environ.get("GATEWAY_SESSION_ID") or os.environ.get("K12_GATEWAY_SESSION")
    if not sid:
        return False
    return await apply_gateway_session(context, sid)
