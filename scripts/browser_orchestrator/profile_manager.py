"""browser_orchestrator.profile_manager — Fingerprint Profile Manager.

Manages realistic, cross-consistent browser fingerprint profiles across
Chromium, Firefox, and Safari targets.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class BrowserProfile:
    name: str
    engine: str  # "firefox" or "chromium"
    user_agent: str
    os_name: str
    sec_ch_ua: Optional[str]
    sec_ch_ua_platform: Optional[str]
    sec_ch_ua_mobile: Optional[str]
    accept_language: str
    viewport_width: int
    viewport_height: int
    device_scale_factor: float
    locale: str
    timezone_id: str


PROFILES: list[BrowserProfile] = [
    BrowserProfile(
        name="chrome_win_136",
        engine="chromium",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        os_name="windows",
        sec_ch_ua='"Chromium";v="136", "Google Chrome";v="136", "Not.A/Brand";v="99"',
        sec_ch_ua_platform='"Windows"',
        sec_ch_ua_mobile="?0",
        accept_language="en-US,en;q=0.9",
        viewport_width=1920,
        viewport_height=1080,
        device_scale_factor=1.0,
        locale="en-US",
        timezone_id="America/New_York",
    ),
    BrowserProfile(
        name="firefox_win_135",
        engine="firefox",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:135.0) Gecko/20100101 Firefox/135.0",
        os_name="windows",
        sec_ch_ua=None,
        sec_ch_ua_platform=None,
        sec_ch_ua_mobile=None,
        accept_language="en-US,en;q=0.5",
        viewport_width=1920,
        viewport_height=1080,
        device_scale_factor=1.0,
        locale="en-US",
        timezone_id="America/New_York",
    ),
    BrowserProfile(
        name="chrome_mac_133",
        engine="chromium",
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        os_name="macos",
        sec_ch_ua='"Not(A:Brand";v="24", "Chromium";v="133", "Google Chrome";v="133"',
        sec_ch_ua_platform='"macOS"',
        sec_ch_ua_mobile="?0",
        accept_language="en-US,en;q=0.9",
        viewport_width=1440,
        viewport_height=900,
        device_scale_factor=2.0,
        locale="en-US",
        timezone_id="America/Los_Angeles",
    ),
]


class FingerprintProfileManager:
    """Selects and maintains coherent browser fingerprint profiles."""

    @staticmethod
    def get_profile(engine: str = "auto", os_name: Optional[str] = None) -> BrowserProfile:
        candidates = PROFILES
        if engine in ("firefox", "camoufox"):
            candidates = [p for p in candidates if p.engine == "firefox"]
        elif engine in ("chromium", "cloak", "nodriver"):
            candidates = [p for p in candidates if p.engine == "chromium"]

        if os_name:
            filtered = [p for p in candidates if p.os_name == os_name.lower()]
            if filtered:
                candidates = filtered

        return random.choice(candidates or PROFILES)
