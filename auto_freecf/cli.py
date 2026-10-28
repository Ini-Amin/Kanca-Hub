#!/usr/bin/env python3
"""Command-line entry point for the Auto-FreeCF package.

Two surfaces live here:

  auto-freecf tui      interactive menu (what `moycf` used to open)
  auto-freecf login    headless/bulk login from a file or a single account

The larger multi-tool surface (`kancahub`) lives in `scripts/kancahub.py` and
is installed by `scripts/install.sh`; it is not part of this Python package.
"""
from __future__ import annotations

import argparse
import sys

from . import __version__


def _cmd_tui(args: argparse.Namespace) -> int:
    # Imported lazily so `--help` works without a browser stack installed.
    from .tui import main as tui_main

    tui_main()
    return 0


def _cmd_login(args: argparse.Namespace) -> int:
    from .browser_bot import process_accounts
    from .utils import load_accounts, load_proxy_config

    if args.single:
        if ":" not in args.single:
            print("error: --single expects email:password", file=sys.stderr)
            return 2
        email, password = args.single.split(":", 1)
        accounts = [{"email": email.strip(), "password": password.strip()}]
    elif args.accounts:
        accounts = load_accounts(args.accounts)
    else:
        print("error: pass --single email:password or --accounts <file>", file=sys.stderr)
        return 2

    if not accounts:
        print("error: no accounts to process", file=sys.stderr)
        return 1

    proxies = load_proxy_config(args.proxy) if args.proxy else None
    results = process_accounts(
        accounts,
        headless=not args.visible,
        proxies=proxies,
        login_method=args.login_method,
    )
    return 0 if results else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="auto-freecf",
        description="Cloudflare Workers AI account manager (login + token export)",
    )
    ap.add_argument("--version", action="version", version=f"auto-freecf {__version__}")
    sub = ap.add_subparsers(dest="command")

    tui = sub.add_parser("tui", help="interactive terminal menu (default)")
    tui.set_defaults(func=_cmd_tui)

    login = sub.add_parser("login", help="log in to existing accounts and export tokens")
    login.add_argument("--single", metavar="EMAIL:PASSWORD", help="one account")
    login.add_argument("--accounts", metavar="FILE", help="JSON or TXT account file")
    login.add_argument("--proxy", metavar="FILE", help="proxy config JSON")
    login.add_argument("--visible", action="store_true", help="show the browser window")
    login.add_argument(
        "--login-method",
        choices=["email", "google"],
        default="email",
        help="how to authenticate (default: email)",
    )
    login.set_defaults(func=_cmd_login)

    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # No subcommand: keep the historical behaviour of `moycf` (open the menu).
    if not getattr(args, "command", None):
        args.func = _cmd_tui
        args.command = "tui"

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
