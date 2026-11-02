"""browser_orchestrator.adapters.camoufox_adapter — Camoufox Firefox C++ WAF bypass adapter."""
from __future__ import annotations

import os
from typing import Any, Optional

from browser_orchestrator.network_manager import ProxyConfig
from browser_orchestrator.profile_manager import BrowserProfile


class CamoufoxAdapter:
    def __init__(self, headless: bool = True, proxy: Optional[ProxyConfig] = None, profile: Optional[BrowserProfile] = None):
        self.headless = headless
        self.proxy = proxy
        self.profile = profile
        self._cm = None
        self._browser = None

    async def __aenter__(self):
        from camoufox.async_api import AsyncCamoufox

        kw: dict[str, Any] = {
            "headless": self.headless,
            "humanize": True,
            "os": self.profile.os_name if self.profile else "windows",
        }
        if self.proxy:
            kw["proxy"] = self.proxy.to_playwright_dict()
            kw["geoip"] = True

        self._cm = AsyncCamoufox(**kw)
        self._browser = await self._cm.__aenter__()
        return self._browser

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._cm:
            await self._cm.__aexit__(exc_type, exc_val, exc_tb)
