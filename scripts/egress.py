#!/usr/bin/env python3
"""
Egress routing helper for Auto-FreeCF / KancaHub.

Ensures clean, non-blocked egress IP for browser automation (e.g. GitHub signup)
by spawning a background rotating proxy gateway (scripts/proxy_gateway.py) using
verified clean proxies from pool or public feeds.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add scripts directory to path
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from proxy_lib import (
    check_gateway_egress,
    ensure_clean_egress,
    find_free_port,
    get_my_ip,
    stop_gateway,
)

__all__ = [
    "ensure_clean_egress",
    "check_gateway_egress",
    "get_my_ip",
    "stop_gateway",
    "find_free_port",
]


def _cli() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Ensure clean non-blocked egress gateway")
    ap.add_argument("--prefer-pool", default=None, help="preferred pool file")
    ap.add_argument("--target-ip", default=None, help="blocked/forbidden IP to avoid")
    args = ap.parse_args()

    gw, proc = ensure_clean_egress(prefer_pool=args.prefer_pool, target_ip=args.target_ip)
    if not gw:
        print("✗ Could not establish clean egress gateway.", file=sys.stderr)
        return 1

    print(f"\nGateway active at: {gw}")
    print("Press Ctrl+C to terminate gateway...")
    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\nStopping gateway...")
    finally:
        stop_gateway(proc)
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
