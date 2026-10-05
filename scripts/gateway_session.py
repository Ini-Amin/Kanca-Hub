"""Sticky session helper for PetaniProxy (and compatible) gateways.

The PetaniProxy gateway at 127.0.0.1:8888 implements session pinning: sending
header 'X-Session-ID: <id>' pins one upstream proxy for 600s. Without sticky
sessions, a rotating gateway switches exit IP on every HTTP request/connection,
causing anti-bot systems to detect and flag the session mid-flow.

Exposes:
    is_gateway(proxy_url) -> bool
    gateway_headers(session_id) -> dict[str, str]
    apply_gateway_session(page_or_tab, session_id) -> bool
"""

from __future__ import annotations

import asyncio
import os
from typing import Any
from urllib.parse import urlparse

GATEWAY_HEADER = "X-Session-ID"
DEFAULT_GATEWAY_HOSTS = ("127.0.0.1:8888", "localhost:8888", "127.0.0.1:8899", "localhost:8899")


def is_gateway(proxy_url: str | None = None) -> bool:
    """Return True if proxy_url points to a local rotating gateway or gateway mode is enabled."""
    if os.environ.get("GATEWAY_ACTIVE") == "1":
        return True
    if not proxy_url:
        return False
    clean = str(proxy_url).strip().lower()
    return any(host in clean for host in DEFAULT_GATEWAY_HOSTS)


def get_current_session_id() -> str | None:
    """Retrieve session id from environment if present."""
    return os.environ.get("GATEWAY_SESSION_ID") or os.environ.get("K12_GATEWAY_SESSION")


def gateway_headers(session_id: str | None = None) -> dict[str, str]:
    """Return the sticky session headers dictionary for HTTP clients.

    Returns empty dict if session_id is None and no environment session is set.
    """
    sid = session_id or get_current_session_id()
    if sid:
        return {GATEWAY_HEADER: str(sid).strip()}
    return {}


async def _watch_nodriver_browser(browser: Any, session_id: str) -> None:
    """Background task patching new page targets in a nodriver browser."""
    try:
        import nodriver as uc
    except ImportError:
        return

    patched: set[int] = set()
    while getattr(browser, "connection", None) and not getattr(browser, "stopped", False):
        try:
            targets = list(getattr(browser, "targets", []) or [])
            for target in targets:
                if getattr(target, "type_", None) == "page" and id(target) not in patched:
                    patched.add(id(target))
                    try:
                        await target.send(uc.cdp.network.enable())
                        headers_obj = uc.cdp.network.Headers({GATEWAY_HEADER: str(session_id).strip()})
                        await target.send(uc.cdp.network.set_extra_http_headers(headers=headers_obj))
                    except Exception:
                        patched.discard(id(target))
        except Exception:
            pass
        await asyncio.sleep(0.5)


async def apply_gateway_session(page_or_tab: Any, session_id: str | None = None) -> bool:
    """Apply X-Session-ID extra HTTP header to a browser page/tab or context.

    Supports:
      - nodriver Tab: sends CDP Network.enable and Network.setExtraHTTPHeaders
      - nodriver Browser: sets headers on existing targets and starts background watcher
      - Playwright / Patchright Page or BrowserContext: calls set_extra_http_headers()
    """
    sid = session_id or get_current_session_id()
    if not sid or page_or_tab is None:
        return False

    sid_str = str(sid).strip()
    applied = False

    # 1. Playwright / Patchright Page or BrowserContext
    if hasattr(page_or_tab, "set_extra_http_headers"):
        try:
            await page_or_tab.set_extra_http_headers({GATEWAY_HEADER: sid_str})
            applied = True
        except Exception:
            pass

    # Playwright page's context
    if hasattr(page_or_tab, "context") and hasattr(page_or_tab.context, "set_extra_http_headers"):
        try:
            await page_or_tab.context.set_extra_http_headers({GATEWAY_HEADER: sid_str})
            applied = True
        except Exception:
            pass

    # 2. nodriver Tab (has .send)
    if hasattr(page_or_tab, "send"):
        try:
            import nodriver as uc
            await page_or_tab.send(uc.cdp.network.enable())
            headers_obj = uc.cdp.network.Headers({GATEWAY_HEADER: sid_str})
            await page_or_tab.send(uc.cdp.network.set_extra_http_headers(headers=headers_obj))
            applied = True
        except Exception:
            pass

    # 3. nodriver Browser (has .targets)
    if hasattr(page_or_tab, "targets"):
        try:
            import nodriver as uc
            targets = list(getattr(page_or_tab, "targets", []) or [])
            for target in targets:
                if getattr(target, "type_", None) == "page" and hasattr(target, "send"):
                    try:
                        await target.send(uc.cdp.network.enable())
                        headers_obj = uc.cdp.network.Headers({GATEWAY_HEADER: sid_str})
                        await target.send(uc.cdp.network.set_extra_http_headers(headers=headers_obj))
                        applied = True
                    except Exception:
                        pass
            # Start background watcher for future tabs
            if not getattr(page_or_tab, "_gw_session_watcher", None):
                page_or_tab._gw_session_watcher = asyncio.create_task(
                    _watch_nodriver_browser(page_or_tab, sid_str)
                )
        except Exception:
            pass

    return applied
