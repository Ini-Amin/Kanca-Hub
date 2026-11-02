"""browser_orchestrator.session_manager — Browser Session Manager & Unified Page Wrapper.

Provides a uniform, high-level API over Playwright (Camoufox/Cloak) and nodriver tabs:
- page.goto(url)
- page.fill(selector, text)
- page.click(selector)
- page.text_content(selector)
- page.screenshot(path)
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from browser_orchestrator.network_manager import ProxyConfig
from browser_orchestrator.profile_manager import BrowserProfile
from browser_orchestrator.telemetry import SessionTelemetry


class UnifiedPage:
    """Wraps raw Playwright page or nodriver tab in a uniform interface."""

    def __init__(self, raw_page: Any, is_nodriver: bool, telemetry: SessionTelemetry):
        self._raw = raw_page
        self._is_nodriver = is_nodriver
        self._telemetry = telemetry

    @property
    def raw(self) -> Any:
        return self._raw

    @property
    def url(self) -> str:
        if self._is_nodriver:
            return getattr(self._raw, "url", "")
        return self._raw.url

    async def goto(self, url: str, timeout: float = 45.0, wait_until: str = "domcontentloaded") -> Any:
        self._telemetry.log_event("navigation", url)
        if self._is_nodriver:
            return await self._raw.get(url)
        return await self._raw.goto(url, timeout=int(timeout * 1000), wait_until=wait_until)

    async def fill(self, selector: str, text: str, timeout: float = 15.0) -> None:
        self._telemetry.log_event("action", self.url, action="fill", selector=selector)
        if self._is_nodriver:
            el = await self._raw.select(selector, timeout=timeout)
            if el:
                await el.send_keys(text)
            return
        await self._raw.fill(selector, text, timeout=int(timeout * 1000))

    async def click(self, selector: str, timeout: float = 15.0) -> None:
        self._telemetry.log_event("action", self.url, action="click", selector=selector)
        if self._is_nodriver:
            el = await self._raw.select(selector, timeout=timeout)
            if el:
                await el.click()
            return
        await self._raw.click(selector, timeout=int(timeout * 1000))

    async def inner_text(self, selector: str = "body") -> str:
        if self._is_nodriver:
            el = await self._raw.select(selector)
            return el.text if el else ""
        return await self._raw.inner_text(selector)

    async def wait_for_timeout(self, ms: int) -> None:
        if self._is_nodriver:
            await asyncio.sleep(ms / 1000.0)
        else:
            await self._raw.wait_for_timeout(ms)

    async def screenshot(self, path: str) -> None:
        self._telemetry.log_event("action", self.url, action="screenshot", path=path)
        if self._is_nodriver:
            await self._raw.save_screenshot(path)
        else:
            await self._raw.screenshot(path=path)


class BrowserSessionManager:
    """Manages session lifecycle, active adapter connection, and telemetry binding."""

    def __init__(self, adapter: Any, is_nodriver: bool, telemetry: SessionTelemetry):
        self.adapter = adapter
        self.is_nodriver = is_nodriver
        self.telemetry = telemetry
        self._browser = None
        self._page = None

    async def __aenter__(self) -> UnifiedPage:
        self._browser = await self.adapter.__aenter__()
        if self.is_nodriver:
            tab = self._browser.main_tab
            self._page = UnifiedPage(tab, is_nodriver=True, telemetry=self.telemetry)
        else:
            # Playwright browser or context
            if hasattr(self._browser, "new_page"):
                page = await self._browser.new_page()
            else:
                # Browser object
                page = await self._browser.new_page()
            self._page = UnifiedPage(page, is_nodriver=False, telemetry=self.telemetry)
        return self._page

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._page and not self.is_nodriver and hasattr(self._page.raw, "close"):
            try:
                await self._page.raw.close()
            except Exception:
                pass
        if self.adapter:
            await self.adapter.__aexit__(exc_type, exc_val, exc_tb)
        self.telemetry.ended_at = asyncio.get_event_loop().time()
