"""commands_github — GitHub account farm for kancahub."""
from __future__ import annotations

import argparse
from typing import Any


def map_github_farm_args(a: Any, choice: Any = None) -> list[str]:
    """Map kancahub github farm CLI arguments to scripts/github_farm.py flags.

    Pure string work, no network: the caller resolves the egress once via
    `_choose_egress(...)` and passes it in as `choice`. Legacy default values
    (proxy=None/auto) mean "leave the child's built-in egress alone", which is
    what keeps offline callers (tests, other tools) side-effect free.
    """
    import kancahub
    cmd: list[str] = []
    if getattr(a, "index", None) is not None:
        cmd += ["--index", str(a.index)]
    # Translate --domain to github_farm.py's --email-domain
    domain = getattr(a, "domain", None)
    if domain:
        cmd += ["--email-domain", domain]
    inbox = getattr(a, "inbox", None)
    if inbox:
        cmd += ["--inbox", inbox]
    max_acc = getattr(a, "max_accounts", None)
    if max_acc is not None:
        cmd += ["--max-accounts", str(max_acc)]
    # Pacing: explicit --delay-min/--delay-max ALWAYS win; otherwise apply the
    # --pace preset (default 'normal' = 60-90s, ~1-1.5 min/account).
    pace = getattr(a, "pace", None)
    delay_min = getattr(a, "delay_min", None)
    delay_max = getattr(a, "delay_max", None)
    if delay_min is None and delay_max is None and pace in kancahub.PACE_PRESETS:
        delay_min, delay_max = kancahub.PACE_PRESETS[pace]
    if delay_min is not None:
        cmd += ["--delay-min", str(delay_min)]
    if delay_max is not None:
        cmd += ["--delay-max", str(delay_max)]
    retries = getattr(a, "retries", None)
    if retries:
        cmd += ["--retries", str(retries)]
    if choice is None:
        mode = getattr(a, "proxy", None)
        if getattr(a, "no_proxy", False):
            choice = kancahub.EgressChoice(None, "none", direct=True)
        elif mode and mode.lower() not in (kancahub.PROXY_AUTO, kancahub.PROXY_NONE, "direct"):
            # An explicit proxy URL is pure data — forward it without probing.
            choice = kancahub.EgressChoice(mode, "explicit")
        elif mode and mode.lower() == kancahub.PROXY_NONE:
            choice = kancahub.EgressChoice(None, "none", direct=True)
        # else: legacy call (no resolved choice) -> do not touch the child's egress
    # An explicit hop must win over the child's pool, or github_farm.py would
    # silently pick a different (pool) egress than the one we just verified.
    if choice is not None and choice.proxy and getattr(a, "pool", None):
        print(kancahub.col("yellow", "• --pool ignored: an explicit/auto proxy already selects the egress"))
    elif getattr(a, "pool", None):
        cmd += ["--pool", a.pool]
    if choice is not None:
        cmd += kancahub._proxy_flags(choice, supports_no_proxy=True)
    if getattr(a, "headless", False):
        cmd.append("--headless")
    if getattr(a, "dry_run", False):
        cmd.append("--dry-run")
    return cmd


def build_github_parser(sub: Any = None) -> argparse.ArgumentParser:
    """Build the argument parser for 'kancahub github' and its subcommands."""
    import kancahub
    if sub is None:
        ghp = argparse.ArgumentParser(
            prog="kancahub github",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description="GitHub account farm (our domain / BINUS)",
        )
    elif hasattr(sub, "add_parser"):
        ghp = sub.add_parser(
            "github",
            help="GitHub account farm (our domain / BINUS)",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            description=(
                "GitHub account farm:\n"
                "\n"
                "  1. farm      Create GitHub account(s) using our domain (kancalabs.biz.id / kancalabs.my.id)\n"
                "               via KancaHub mail relay OR the BINUS school mailbox.\n"
                "  2. check     Verify environment dependencies, Camoufox, and mailbox/relay credentials.\n"
                "\n"
                "Examples:\n"
                "  kancahub github farm --domain bizid --max-accounts 3\n"
                "  kancahub github farm --domain binus --index 1 --headless\n"
                "  kancahub github check"
            ),
        )
    else:
        ghp = sub

    ghs = ghp.add_subparsers(dest="github_cmd")

    # ---- farm ----
    gf = ghs.add_parser(
        "farm",
        help="sign up GitHub account(s) using our domain or BINUS mailbox",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Create GitHub account(s) with automated form fill & OTP handling.\n"
            "\n"
            "Domain choices:\n"
            "  --domain bizid   gh<rand>@kancalabs.biz.id (via KancaHub mail relay, default relay)\n"
            "  --domain myid    gh<rand>@kancalabs.my.id  (via KancaHub mail relay)\n"
            "  --domain binus   raymondi+gh<N>@binus.ac.id (school mailbox, default)\n"
            "\n"
            "Rate limiting policy: 20-45s random delay, concurrency 1, max 5 accounts/run by default.\n"
            "Arkose / CAPTCHA puzzles remain manual if triggered by anti-bot.\n"
            "\n"
            "Examples:\n"
            "  kancahub github farm --domain bizid --max-accounts 3 --headless\n"
            "  kancahub github farm --domain binus --index 2 --dry-run\n"
            "  kancahub github farm --domain myid --proxy http://127.0.0.1:8888"
        ),
    )
    gf.add_argument("--index", type=int, default=1, help="starting N for account address (default: 1)")
    gf.add_argument(
        "--domain",
        default="binus",
        metavar="DOMAIN",
        help="email domain: binus (default, school mailbox), bizid/myid shortcut, or your own domain via the relay",
    )
    gf.add_argument(
        "--inbox",
        choices=["binus", "relay"],
        default="binus",
        help="inbox source: binus (default, Outlook school mailbox) or relay (KancaHub mail relay)",
    )
    gf.add_argument("--headless", action="store_true", help="run browser headless (virtual display / Xvfb)")
    gf.add_argument("--dry-run", action="store_true", help="walk the signup flow, screenshot, do not submit/create")
    gf.add_argument("--proxy", default="auto", metavar="PROXY", help=kancahub.PROXY_HELP)
    gf.add_argument("--pool", default=None, help="proxy list file for rotation")
    gf.add_argument("--retries", type=int, default=0, metavar="N", help="retries on access_restricted block (default: 0)")
    gf.add_argument("--no-proxy", action="store_true", help="alias for --proxy none: force direct connection")
    gf.add_argument("--delay-min", type=float, default=None, metavar="SEC", help="min delay between accounts (overrides --pace)")
    gf.add_argument("--delay-max", type=float, default=None, metavar="SEC", help="max delay between accounts (overrides --pace)")
    gf.add_argument("--pace", choices=sorted(kancahub.PACE_PRESETS), default="normal",
                    help="per-account pacing: fast=20-45s, normal=60-90s (~1-1.5 min/account; avoids the "
                         "'take a breath' throttle), safe=120-180s (default: normal)")
    gf.add_argument("--max-accounts", type=int, default=5, metavar="N", help="max accounts to process in this run (default: 5)")
    gf.add_argument("--mobile-rotate", action="store_true", help="rotate tethered phone carrier IP before run & retry on block")

    # ---- check ----
    gc = ghs.add_parser(
        "check",
        help="check dependencies, browser setup, and mailbox/relay credentials",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Check dependencies and configuration for GitHub farming.\n"
            "\n"
            "Examples:\n"
            "  kancahub github check"
        ),
    )
    gc.add_argument(
        "--domain",
        default="binus",
        metavar="DOMAIN",
        help="domain to check: binus (school mailbox), bizid/myid shortcut, or your own domain via the relay",
    )
    gc.add_argument(
        "--inbox",
        choices=["binus", "relay"],
        default="binus",
        help="inbox to check: binus (Outlook) or relay (KancaHub mail relay)",
    )

    return ghp


def cmd_github(a) -> int:
    """GitHub account farm."""
    import kancahub
    py = kancahub.pick_python()
    py_camo = kancahub.pick_python(camoufox=True)
    sub = getattr(a, "github_cmd", None)
    if not sub:
        print(kancahub.col("yellow", "• no github subcommand specified — run 'kancahub github --help' for usage"))
        return 1

    farm = kancahub.AUTO_FREECF / "scripts" / "github_farm.py"

    if sub == "check":
        if not farm.exists():
            print(kancahub.col("red", f"✗ github_farm.py not found at {farm}"))
            return 1
        cmd = [py_camo, str(farm), "--check"]
        if getattr(a, "domain", None):
            cmd += ["--email-domain", a.domain]
        if getattr(a, "inbox", None):
            cmd += ["--inbox", a.inbox]
        return kancahub.run(cmd, cwd=kancahub.AUTO_FREECF)

    if sub == "farm":
        if not farm.exists():
            print(kancahub.col("red", f"✗ github_farm.py not found at {farm}"))
            return 1
        # Resolve the egress once, at run time (never at parse/import time).
        mode = getattr(a, "proxy", None) or kancahub.PROXY_AUTO
        if getattr(a, "no_proxy", False):
            mode = kancahub.PROXY_NONE
        choice = kancahub._choose_egress(mode, kancahub.EGRESS_TARGETS["github"])
        cmd = [py_camo, str(farm)] + kancahub.map_github_farm_args(a, choice)
        print(kancahub.col("yellow", "Note: Anti-bot CAPTCHA puzzles and Education attestation remain manual steps if encountered."))
        try:
            return kancahub.run_with_mobile_retry(cmd, cwd=kancahub.AUTO_FREECF,
                                                 mobile_rotate=getattr(a, "mobile_rotate", False),
                                                 account=f"github:{getattr(a, 'index', 1)}")
        finally:
            kancahub._stop_auto_gateways()

    print(kancahub.col("red", f"✗ unknown github command: {sub}"))
    return 1
