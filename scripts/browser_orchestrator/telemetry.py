"""browser_orchestrator.telemetry — Telemetry & Test Results Logger.

Captures navigation events, detection signals, timing, and forensic test results.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class TelemetryEvent:
    timestamp: float
    kind: str  # navigation, action, detection, response
    url: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class SessionTelemetry:
    session_id: str
    engine: str
    started_at: float
    ended_at: Optional[float] = None
    exit_ip: Optional[str] = None
    events: list[TelemetryEvent] = field(default_factory=list)
    verdict: str = "PENDING"  # PASSED, DETECTED_BOT, CHALLENGED, FAILED
    error: Optional[str] = None

    def log_event(self, kind: str, url: str, **kwargs: Any) -> None:
        self.events.append(TelemetryEvent(
            timestamp=time.time(),
            kind=kind,
            url=url,
            details=kwargs,
        ))

    def save_report(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")


class TelemetryManager:
    @staticmethod
    def create_session(session_id: str, engine: str) -> SessionTelemetry:
        return SessionTelemetry(
            session_id=session_id,
            engine=engine,
            started_at=time.time(),
        )
