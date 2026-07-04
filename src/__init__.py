#!/usr/bin/env python3
"""Auto-FreeCF - Cloudflare Workers AI Account & Token Grabber"""

from .browser_bot import CFAutoGrabber, process_accounts
from .turnstile_solver import extract_sitekey, solve_turnstile_isolated, solve_turnstile_manual
from .utils import load_accounts, load_proxy_config, save_results

__version__ = "3.3.14"
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
