"""browser_orchestrator — Multi-engine browser automation orchestrator.

Implements the multi-adapter architecture:
- Camoufox Adapter (Firefox C++ spoofing)
- CloakBrowser Adapter (Chromium C++ spoofing)
- nodriver Adapter (Undetected async CDP)
- Unified Page & Session Management
- Fingerprint, Proxy, and Telemetry integration
"""
from browser_orchestrator.network_manager import ProxyConfig, ProxyNetworkManager
from browser_orchestrator.orchestrator import BrowserOrchestrator
from browser_orchestrator.profile_manager import BrowserProfile, FingerprintProfileManager
from browser_orchestrator.session_manager import BrowserSessionManager, UnifiedPage
from browser_orchestrator.telemetry import SessionTelemetry, TelemetryManager

__all__ = [
    "BrowserOrchestrator",
    "BrowserProfile",
    "BrowserSessionManager",
    "FingerprintProfileManager",
    "ProxyConfig",
    "ProxyNetworkManager",
    "SessionTelemetry",
    "TelemetryManager",
    "UnifiedPage",
]
