#!/usr/bin/env python3
"""Auto-FreeCF - Cloudflare Workers AI Account & Token Grabber.

Heavy dependencies (patchright) are imported lazily so that `--help`,
`--version` and tooling that only needs metadata work on a bare interpreter.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

__version__ = "4.3.1"

__all__ = [
    "CFAutoGrabber",
    "process_accounts",
    "extract_sitekey",
    "solve_turnstile_isolated",
    "solve_turnstile_manual",
    "load_accounts",
    "load_proxy_config",
    "save_results",
]

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .browser_bot import CFAutoGrabber, process_accounts
    from .turnstile_solver import (
        extract_sitekey,
        solve_turnstile_isolated,
        solve_turnstile_manual,
    )
    from .utils import load_accounts, load_proxy_config, save_results


def __getattr__(name: str) -> Any:
    """Resolve public names on first access (PEP 562)."""
    if name in ("CFAutoGrabber", "process_accounts"):
        from . import browser_bot

        return getattr(browser_bot, name)
    if name in ("extract_sitekey", "solve_turnstile_isolated", "solve_turnstile_manual"):
        from . import turnstile_solver

        return getattr(turnstile_solver, name)
    if name in ("load_accounts", "load_proxy_config", "save_results"):
        from . import utils

        return getattr(utils, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
