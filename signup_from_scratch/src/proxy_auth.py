"""Proxy authentication for nodriver via CDP Fetch domain.

Chrome's ``--proxy-server`` flag cannot carry credentials, and MV2/MV3 auth
extensions are unreliable in headless mode. The robust approach:

  1. launch Chrome with ``--proxy-server=http://host:port`` (no credentials)
  2. on each page tab, enable the CDP ``Fetch`` domain (handle_auth_requests)
  3. answer ``Fetch.authRequired`` events with username/password

Works headlessly for HTTP/HTTPS (and auth-capable SOCKS) proxies.

Public API:
    browser, handler = await start_browser_with_proxy(proxy_url, headless=True)
    ... use browser ...
    if handler: handler.stop()
    browser.stop()

    # For callers that create the browser themselves:
    handler = ProxyAuthHandler("user", "pass")
    await handler.attach(browser)          # watches + patches every tab
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

import nodriver as uc


def parse_proxy_url(proxy_url: str) -> dict:
    """Split a proxy URL into {scheme, host, port, username, password}."""
    if not proxy_url:
        return {}

    # host:port:user:pass
    if "://" not in proxy_url and proxy_url.count(":") == 3:
        host, port, user, pw = proxy_url.split(":", 3)
        return {"scheme": "http", "host": host, "port": int(port),
                "username": user, "password": pw}

    if "://" not in proxy_url:
        proxy_url = "http://" + proxy_url

    u = urlparse(proxy_url)
    return {
        "scheme": (u.scheme or "http").lower(),
        "host": u.hostname or "",
        "port": u.port or 0,
        "username": u.username or "",
        "password": u.password or "",
    }


def proxy_server_arg(proxy_url: str) -> str | None:
    """Return the ``--proxy-server`` value (scheme://host:port), or None."""
    info = parse_proxy_url(proxy_url)
    if not info or not info.get("host"):
        return None
    return f"{info['scheme']}://{info['host']}:{info['port']}"


class ProxyAuthHandler:
    """Answers CDP proxy auth challenges for all page tabs of a browser."""

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self._browser: uc.Browser | None = None
        self._watch_task: asyncio.Task | None = None
        self._patched: set = set()
        self._running = False

    async def attach(self, browser: uc.Browser) -> None:
        self._browser = browser
        self._running = True
        self._watch_task = asyncio.create_task(self._watch())
        # Patch whatever tabs already exist.
        await self._patch_existing()

    async def _patch_existing(self) -> None:
        assert self._browser is not None
        for tab in list(getattr(self._browser, "targets", []) or []):
            if getattr(tab, "type_", None) == "page":
                await self._patch_tab(tab)

    async def _watch(self) -> None:
        while self._running:
            try:
                for tab in list(getattr(self._browser, "targets", []) or []):
                    if getattr(tab, "type_", None) == "page":
                        await self._patch_tab(tab)
            except Exception:
                pass
            await asyncio.sleep(0.8)

    async def _patch_tab(self, tab) -> None:
        key = id(tab)
        if key in self._patched:
            return
        self._patched.add(key)
        try:
            await tab.send(uc.cdp.fetch.enable(handle_auth_requests=True))
        except Exception:
            self._patched.discard(key)
            return

        async def on_auth(event) -> None:
            try:
                resp = uc.cdp.fetch.AuthChallengeResponse(
                    response="ProvideCredentials",
                    username=self.username,
                    password=self.password,
                )
                await tab.send(
                    uc.cdp.fetch.continue_with_auth(
                        request_id=event.request_id,
                        auth_challenge_response=resp,
                    )
                )
            except Exception:
                pass

        try:
            tab.add_handler(uc.cdp.fetch.AuthRequired, on_auth)
        except Exception:
            pass

    def stop(self) -> None:
        self._running = False
        if self._watch_task:
            self._watch_task.cancel()


async def start_browser_with_proxy(
    proxy_url: str,
    *,
    headless: bool = False,
    sandbox: bool = False,
    lang: str = "en-US",
) -> tuple[uc.Browser, "ProxyAuthHandler | None"]:
    """Launch Chrome routed through `proxy_url`, handling auth via CDP."""
    info = parse_proxy_url(proxy_url) if proxy_url else {}
    browser_args = []
    server = proxy_server_arg(proxy_url) if proxy_url else None
    if server:
        browser_args.append(f"--proxy-server={server}")

    browser = await uc.start(
        headless=headless,
        sandbox=sandbox,
        lang=lang,
        browser_args=browser_args or None,
    )

    handler = None
    if info and info.get("username"):
        handler = ProxyAuthHandler(info["username"], info["password"])
        await handler.attach(browser)

    return browser, handler
