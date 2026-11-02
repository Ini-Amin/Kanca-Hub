"""browser_orchestrator.orchestrator — Unified Browser Orchestrator.

Implements the multi-engine adapter architecture:
AI Agent / Automation Engine
   └── Browser Orchestrator
         ├── Camoufox Adapter   (Firefox C++ spoofing)
         ├── CloakBrowser Adapter (Chromium C++ spoofing)
         └── nodriver Adapter   (Undetected async CDP)
               └── Browser Session Manager
                     ├── Fingerprint Profile Manager
                     ├── Proxy / Network Manager
                     └── Telemetry & Test Results
"""
from __future__ import annotations

import uuid
from typing import Any, AsyncIterator, Optional

from browser_orchestrator.adapters.camoufox_adapter import CamoufoxAdapter
from browser_orchestrator.adapters.cloak_adapter import CloakBrowserAdapter
from browser_orchestrator.adapters.nodriver_adapter import NodriverAdapter
from browser_orchestrator.network_manager import ProxyConfig, ProxyNetworkManager
from browser_orchestrator.profile_manager import BrowserProfile, FingerprintProfileManager
from browser_orchestrator.session_manager import BrowserSessionManager, UnifiedPage
from browser_orchestrator.telemetry import SessionTelemetry, TelemetryManager


class BrowserOrchestrator:
    """Orchestrates browser automation sessions across Camoufox, Cloak, and nodriver engines."""

    @classmethod
    def session(
        cls,
        engine: str = "auto",  # "auto", "camoufox", "cloak", "nodriver"
        headless: bool = True,
        proxy: Optional[str] = None,
        country: Optional[str] = None,
        os_name: Optional[str] = None,
    ) -> BrowserSessionManager:
        """Create a managed unified browser session with automatic profile and proxy routing."""
        chosen_engine = engine.lower().strip()
        if chosen_engine == "auto":
            # Default best anti-detect engine is Camoufox; fallback to Cloak or nodriver
            chosen_engine = "camoufox"

        profile = FingerprintProfileManager.get_profile(engine=chosen_engine, os_name=os_name)
        proxy_cfg = ProxyNetworkManager.resolve_proxy(proxy_str=proxy, country=country)
        session_id = f"sess_{uuid.uuid4().hex[:12]}"
        telemetry = TelemetryManager.create_session(session_id=session_id, engine=chosen_engine)

        if chosen_engine in ("camoufox", "firefox"):
            adapter = CamoufoxAdapter(headless=headless, proxy=proxy_cfg, profile=profile)
            is_nodriver = False
        elif chosen_engine in ("cloak", "cloakbrowser", "chromium"):
            adapter = CloakBrowserAdapter(headless=headless, proxy=proxy_cfg, profile=profile)
            is_nodriver = False
        elif chosen_engine in ("nodriver", "cdp"):
            adapter = NodriverAdapter(headless=headless, proxy=proxy_cfg, profile=profile)
            is_nodriver = True
        else:
            adapter = CamoufoxAdapter(headless=headless, proxy=proxy_cfg, profile=profile)
            is_nodriver = False

        return BrowserSessionManager(adapter=adapter, is_nodriver=is_nodriver, telemetry=telemetry)
