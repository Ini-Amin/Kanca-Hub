#!/usr/bin/env python3
"""scripts/us_egress_relay.py — Persistent local HTTP proxy relay for US egress (:25350).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import egress

PORT = int(os.environ.get("US_RELAY_PORT", 25350))


def main() -> None:
    print(f"[*] Discovering US Windscribe proxy candidate...")
    cands = egress.windscribe_candidates(cc="US", limit=3)
    if not cands:
        sys.exit("[✗] No US candidates found.")
    upstream = cands[0]
    print(f"[✓] Selected upstream: {upstream.split('@')[-1]}")
    print(f"[*] Starting HTTP CONNECT relay on http://127.0.0.1:{PORT}...")
    egress.relay(PORT, upstream, bind="127.0.0.1")


if __name__ == "__main__":
    main()
