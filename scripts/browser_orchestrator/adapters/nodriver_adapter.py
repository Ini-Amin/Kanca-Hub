"""browser_orchestrator.adapters.nodriver_adapter — nodriver Undetected Async CDP adapter."""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from browser_orchestrator.network_manager import ProxyConfig
from browser_orchestrator.profile_manager import BrowserProfile


class NodriverAdapter:
    def __init__(self, headless: bool = True, proxy: Optional[ProxyConfig] = None, profile: Optional[BrowserProfile] = None):
        self.headless = headless
        self.proxy = proxy
        self.profile = profile
        self._browser = None

    async def __aenter__(self):
        import nodriver

        browser_args = []
        if self.proxy:
            browser_args.append(self.proxy.to_chrome_arg())
        if self.profile and self.profile.user_agent:
            browser_args.append(f"--user-agent={self.profile.user_agent}")

        self._browser = await nodriver.start(
            headless=self.headless,
            browser_args=browser_args if browser_args else None,
            sandbox=False,
        )
        return self._browser

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._browser:
            try:
                self._browser.stop()
            except Exception:
                pass
