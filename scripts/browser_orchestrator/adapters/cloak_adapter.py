"""browser_orchestrator.adapters.cloak_adapter — CloakBrowser Chromium C++ fingerprint spoof adapter."""
from __future__ import annotations

import os
from typing import Any, Optional

from browser_orchestrator.network_manager import ProxyConfig
from browser_orchestrator.profile_manager import BrowserProfile


class CloakBrowserAdapter:
    def __init__(self, headless: bool = True, proxy: Optional[ProxyConfig] = None, profile: Optional[BrowserProfile] = None):
        self.headless = headless
        self.proxy = proxy
        self.profile = profile
        self._ctx = None

    async def __aenter__(self):
        try:
            import cloakbrowser
            # If cloakbrowser binary is installed, use its native async launcher
            if cloakbrowser.binary_info().get("installed"):
                kw: dict[str, Any] = {
                    "headless": self.headless,
                    "stealth_args": True,
                    "humanize": True,
                }
                if self.proxy:
                    kw["proxy"] = self.proxy.raw_url
                self._ctx = await cloakbrowser.launch_context_async(**kw)
                return self._ctx
        except Exception:
            pass

        # Fallback to Playwright Chromium with stealth flags if Cloak binary isn't extracted
        from playwright.async_api import async_playwright
        self._pw = await async_playwright().start()
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ]
        proxy_dict = self.proxy.to_playwright_dict() if self.proxy else None
        browser = await self._pw.chromium.launch(
            headless=self.headless,
            args=args,
            proxy=proxy_dict,
        )
        self._ctx = await browser.new_context(
            user_agent=self.profile.user_agent if self.profile else None,
            viewport={"width": self.profile.viewport_width if self.profile else 1920,
                      "height": self.profile.viewport_height if self.profile else 1080},
        )
        return self._ctx

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._ctx:
            try:
                await self._ctx.close()
            except Exception:
                pass
        if hasattr(self, "_pw") and self._pw:
            await self._pw.stop()
