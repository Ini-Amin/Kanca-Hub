"""menu — unified interactive menu and guided flows for kancahub."""
from __future__ import annotations

import argparse
import sys

# Menu keys that start LONG-RUNNING servers/loops -> run as background jobs.
# (Everything else is a normal, quick, interactive command.)
MENU_BACKGROUND: dict[str, str] = {
    "2": "gateway",     # Start the proxy gateway  (PetaniProxy --serve/--daemon)
    "4": "harvest",     # Harvest proxies          (long harvest)
}

# The argv each background menu item runs (as its own process). These must be
# NON-INTERACTIVE so running them with no stdin never blocks or errors.
MENU_BACKGROUND_CMD: dict[str, list[str]] = {
    "2": ["proxy", "gateway", "--port", "8888", "--target", "30"],  # rotating gateway
    "4": ["proxy", "harvest", "--target", "20"],                    # long harvest
}

UNIFIED_MENU: list[tuple[str, str, str, list[str] | None]] = [
    # Top option: guided pipeline
    ("0", "Run end-to-end setup (guided pipeline)", "end-to-end", None),

    # Group 1-2: Egress & Diagnostics
    ("1", "Check everything is healthy", "doctor", ["doctor"]),
    ("2", "Start the proxy gateway (background)", "proxy gateway", ["proxy", "gateway"]),
    ("3", "Manage WARP tunnel", "warp", ["warp"]),
    ("4", "Harvest proxies (background)", "proxy harvest", ["proxy", "harvest"]),

    # Group 3: Accounts (github/thk/gmail/k12/grok/stack)
    ("5", "GitHub Education account farm", "github farm", ["github", "farm"]),
    ("6", "Create Cloudflare accounts + tokens", "stack signup", ["stack", "signup"]),
    ("7", "Login to existing Cloudflare accounts", "stack login", ["stack", "login"]),
    ("8", "Create TokenHarbor keys", "thk batch", ["thk", "batch"]),
    ("9", "Grok farm", "grok run", ["grok", "run"]),
    ("10", "K-12 teacher verification", "k12 auto", ["k12", "auto"]),
    ("11", "Gmail account farm", "gmail farm", ["gmail", "farm"]),

    # Group 4: 9Router (inject/sync)
    ("12", "Inject Grok tokens into 9Router", "grok inject", ["grok", "inject"]),
    ("13", "Prune dead 9Router connections", "stack sync --prune", ["stack", "sync", "--prune"]),
    ("14", "Wire TokenHarbor env (harbor)", "thk setup-env", ["thk", "setup-env"]),

    # Group 5: Mail & Docs
    ("15", "School mailbox: test login", "mail test", ["mail", "test"]),
    ("16", "School mailbox: wait for OTP", "mail otp", ["mail", "otp"]),
    ("17", "Find SheerID links", "k12 link-finder", ["k12", "link-finder"]),
    ("18", "List teacher-doc countries", "yowes list", ["yowes", "list"]),

    # Group 6: 9Router IDE interception
    ("19", "9Router MITM proxy status (route IDE traffic)", "9router mitm status", ["9router", "mitm", "status"]),

    # Group 7: Adapt any site
    ("20", "Autofarm any website (paste URL -> adapt a pipeline)", "autofarm", ["autofarm"]),

    # Group 8: OTP mailboxes
    ("21", "Cheap OTP mailboxes (Litensi pay-per-code)", "otp", ["otp", "profile"]),

    # Group 9: Vendor farms (vendored tools in ~/Auto-FreeCF-vendor-refs)
    ("22", "Abliteration.ai accounts + API keys", "abliteration run", ["abliteration", "run"]),
    ("23", "ZCode: z.ai Start Plan -> glm pool", "zcode claim --connect", ["zcode", "claim", "--", "--connect"]),
]

MENU_STAGE_HEADERS: dict[str, str] = {
    "0": "[0] Guided Pipeline Setup",
    "1": "[1-2] Egress & Diagnostics (proxy · WARP · doctor)",
    "5": "[3] Account Farms (GitHub · Cloudflare · TokenHarbor · Grok · K-12 · Gmail)",
    "12": "[4] 9Router Integration (inject · sync · prune)",
    "15": "[5] Mailbox & Verification Docs (mail · SheerID · docs)",
    "19": "[6] IDE Interception (MITM · Antigravity/Copilot/Kiro)",
    "20": "[7] Adapt Any Website (autofarm)",
    "21": "[8] OTP Mailboxes (Litensi)",
    "22": "[9] Vendor Farms (abliteration · ZCode)",
}

# Menu keys that are account farms -> the menu asks for count/pace/egress in-CLI.
MENU_FARM_KEYS = {"5", "8", "9", "10", "11", "7"}


def render_menu() -> str:
    """Render the unified menu as text (non-interactive, testable)."""
    import kancahub
    lines = []
    lines.append(f" {kancahub.C['bold']}Unified Command Menu:{kancahub.C['reset']}")
    for key, desc, cmd_str, _ in kancahub.UNIFIED_MENU:
        if key in kancahub.MENU_STAGE_HEADERS:
            lines.append(f"\n {kancahub.C['bold']}{kancahub.C['cyan']}── {kancahub.MENU_STAGE_HEADERS[key]} ──{kancahub.C['reset']}")
        cmd_part = f" {kancahub.C['cyan']}({cmd_str}){kancahub.C['reset']}" if cmd_str else ""
        lines.append(f"  [{kancahub.col('bold', key)}] {desc:<42}{cmd_part}")
    lines.append("")
    lines.append(f"  [{kancahub.col('bold', 'h')}] Help & command reference")
    lines.append(f"  [{kancahub.col('bold', 'q')}] Exit")
    lines.append("")
    lines.append(kancahub.col("dim", "  Tip: if a farm is IP-blocked (GitHub/TokenHarbor), tether your phone and"))
    lines.append(kancahub.col("dim", "       re-run with --proxy none (a mobile/carrier IP is the free fix)."))
    return "\n".join(lines)


def run_end_to_end_flow(p: argparse.ArgumentParser, farm_choice: str | None = None) -> int:
    """
    Execute the safe end-to-end pipeline in order:
      (a) Proxy verify / start (egress)
      (b) Chosen account farm
      (c) Inject to 9Router
      (d) Sync / health check

    Stops and reports honest error if any step fails.
    """
    import kancahub
    print(kancahub.col("bold", "\n════════════════════════════════════════════════════════════════"))
    print(kancahub.col("bold", "               KancaHub End-to-End Setup Pipeline                "))
    print(kancahub.col("bold", "════════════════════════════════════════════════════════════════"))
    print(kancahub.col("dim",  "  Safe sequential flow: Egress -> Farm -> 9Router Inject -> Sync\n"))

    farms = [
        ("1", "GitHub Education", ["github", "farm"], None, ["stack", "sync"]),
        ("2", "Cloudflare Workers AI", ["stack", "signup"], ["stack", "inject"], ["stack", "sync"]),
        ("3", "TokenHarbor AI", ["thk", "batch"], ["thk", "inject"], ["thk", "sync"]),
        ("4", "Grok / xAI", ["grok", "run"], ["grok", "inject"], ["stack", "sync"]),
        ("5", "K-12 Teacher Verification", ["k12", "auto"], ["k12", "inject"], ["k12", "sync"]),
        ("6", "Gmail Farm", ["gmail", "farm"], None, ["stack", "sync"]),
        ("7", "Abliteration.ai accounts + API keys", ["abliteration", "run"], None, ["stack", "sync"]),
        ("8", "ZCode: z.ai Start Plan -> glm pool", ["zcode", "claim", "--", "--connect"], None, ["stack", "sync"]),
    ]

    if farm_choice is None:
        print(kancahub.col("bold", "  Choose target farm for this pipeline run:"))
        for f_key, f_name, f_cmd, _, _ in farms:
            print(f"    [{kancahub.col('bold', f_key)}] {f_name:<28} {kancahub.col('cyan', f'({chr(32).join(f_cmd)})')}")
        print(f"    [{kancahub.col('bold', 'c')}] Cancel / Back to menu\n")

        try:
            f_choice = input(f"  {kancahub.col('bold', f'Select farm [1-{len(farms)}, c]')}: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nPipeline cancelled.")
            return 0
    else:
        f_choice = str(farm_choice).strip().lower()

    if f_choice in ("c", "cancel", "q", "quit", ""):
        print("\nPipeline cancelled.")
        return 0

    selected_farm = next((f for f in farms if f[0] == f_choice), None)
    if not selected_farm:
        print(kancahub.col("yellow", f"✗ Invalid selection '{f_choice}'. Pipeline aborted."))
        return 1

    _, farm_label, farm_args, inject_args, sync_args = selected_farm
    print(kancahub.col("cyan", f"\n▶ Selected pipeline target: {farm_label}\n"))

    # Ask for count/pace/egress IN THE CLI so end-to-end is fully guided too.
    farm_args = kancahub._menu_ask_farm_options(f_choice, list(farm_args))
    print(kancahub.col("dim", f"  → will run: kancahub {' '.join(farm_args)}\n"))

    # ── Step (a): Proxy Egress Verification / Start ──
    print(kancahub.col("bold", "[Step 1/4] Egress check / proxy verification…"))
    rc_egress = kancahub.dispatch(p, p.parse_args(["proxy", "verify"]))
    if rc_egress != 0:
        print(kancahub.col("yellow", "\n• No active proxy masking detected."))
        try:
            start_ans = input(f" {kancahub.col('bold', 'Start rotating proxy gateway on 127.0.0.1:8888 now? [Y/n]')}: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            start_ans = "n"
        if start_ans in ("", "y", "yes"):
            rc_start = kancahub.dispatch(p, p.parse_args(["proxy", "start"]))
            if rc_start != 0:
                print(kancahub.col("red", "\n✗ Step 1 failed: Proxy gateway did not start. Stopping pipeline to avoid IP block."))
                return rc_start
        else:
            print(kancahub.col("red", "\n✗ Step 1 stopped: Clean proxy egress is required for farm operations."))
            return 1
    print(kancahub.col("green", "✓ Step 1 complete: Egress proxy verified.\n"))

    # ── Step (b): Account Farm ──
    print(kancahub.col("bold", f"[Step 2/4] Running {farm_label} ({' '.join(farm_args)})…"))
    rc_farm = kancahub.dispatch(p, p.parse_args(farm_args))
    if rc_farm != 0:
        print(kancahub.col("red", f"\n✗ Step 2 failed: {farm_label} returned exit code {rc_farm}."))
        print(kancahub.col("yellow", "  Stopping pipeline. Resolve issues before injecting to 9Router."))
        return rc_farm
    print(kancahub.col("green", f"✓ Step 2 complete: {farm_label} completed successfully.\n"))

    # ── Step (c): Inject to 9Router ──
    if inject_args and "--inject" in farm_args:
        print(kancahub.col("dim", f"[Step 3/4] Already injected by the batch (--inject). Skipping.\n"))
    elif inject_args:
        print(kancahub.col("bold", f"[Step 3/4] Injecting credentials into 9Router ({' '.join(inject_args)})…"))
        rc_inject = kancahub.dispatch(p, p.parse_args(inject_args))
        if rc_inject != 0:
            print(kancahub.col("red", f"\n✗ Step 3 failed: 9Router injection returned exit code {rc_inject}."))
            print(kancahub.col("yellow", "  Stopping pipeline. Credentials were not registered."))
            return rc_inject
        print(kancahub.col("green", "✓ Step 3 complete: Injected credentials into 9Router.\n"))
    else:
        print(kancahub.col("dim", f"[Step 3/4] 9Router injection not required for {farm_label}. Skipping.\n"))

    # ── Step (d): Sync ──
    if sync_args:
        print(kancahub.col("bold", f"[Step 4/4] Syncing 9Router status ({' '.join(sync_args)})…"))
        rc_sync = kancahub.dispatch(p, p.parse_args(sync_args))
        if rc_sync != 0:
            print(kancahub.col("red", f"\n✗ Step 4 failed: Sync returned exit code {rc_sync}."))
            return rc_sync
        print(kancahub.col("green", "✓ Step 4 complete: Synced with 9Router.\n"))

    print(kancahub.col("green", "════════════════════════════════════════════════════════════════"))
    print(kancahub.col("green", f"✓ End-to-end setup for {farm_label} completed successfully!"))
    print(kancahub.col("green", "════════════════════════════════════════════════════════════════\n"))
    return 0


def _farm_kind(key: str, argv: list[str]) -> str:
    """Normalize a menu key / argv into a farm kind: github|thk|grok|gmail|other."""
    joined = " ".join(argv)
    if "github" in joined:
        return "github"
    if "thk" in joined:
        return "thk"
    if "grok" in joined:
        return "grok"
    if "gmail" in joined:
        return "gmail"
    return "other"


def _menu_ask_farm_options(key: str, argv: list[str]) -> list[str]:
    """Prompt IN THE CLI for common farm options and append them to argv.

    Keeps everything inside `kancahub` (no need to remember flags). Every prompt
    has a default, so just pressing Enter keeps sensible behavior. Works for both
    the unified-menu keys and the end-to-end pipeline keys.
    """
    import kancahub
    def _ask(prompt: str, default: str) -> str:
        try:
            v = input(f"   {kancahub.col('bold', prompt)} [{default}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return default
        return v or default

    kind = kancahub._farm_kind(key, argv)
    if kind == "other":
        return argv

    print(kancahub.col("dim", "   (Enter = keep default; these run inside this CLI)"))

    # 1) how many accounts
    n = _ask("how many accounts", "1")
    if kind == "thk":
        argv = ["thk", "batch", n]
    elif kind == "grok":
        argv = ["grok", "run", "-n", n]
    elif kind == "gmail":
        argv = ["gmail", "farm", "--count", n]
    elif kind == "github":
        argv = [a for a in argv if a not in ("--max-accounts",)]  # drop dupes if re-run
        if n.isdigit() and n != "1":
            argv += ["--max-accounts", n]

    # 1b) thk only: per-IP cap, inject into 9Router, where to store
    if kind == "thk":
        print(kancahub.col("dim", f"   (TokenHarbor allows ~{kancahub.THK_PER_IP_MAX} signups per IP, then locks it ~1h; "
                                  f"{kancahub.THK_PER_IP_DEFAULT} is safe — the IP rotates automatically after that)"))
        per = _ask(f"accounts per IP before rotating (1-{kancahub.THK_PER_IP_MAX})", str(kancahub.THK_PER_IP_DEFAULT))
        if per.isdigit():
            argv += ["--per-ip", str(max(1, min(int(per), kancahub.THK_PER_IP_MAX)))]
        inj = _ask("Inject into 9Router (bulk) when done? (y/n)", "y").lower()
        if inj in ("y", "yes"):
            argv += ["--inject"]
        store = _ask("Store into (9router/ledger/both)", "both").lower()
        if store in ("9router", "ledger", "both"):
            argv += ["--store", store]

    # 2) pacing (github only supports --pace today; thk is limited by a per-IP count, not speed)
    if kind != "thk":
        pace = _ask("pace (fast/normal/safe)", "normal")
        if kind == "github" and pace in ("fast", "normal", "safe"):
            argv += ["--pace", pace]

    # 3) egress  (auto = best available ladder incl. mobile; none = your raw connection)
    egress = _ask("egress — auto=best-available(incl. phone) / none=direct / mobile / warp", "auto")
    if egress == "mobile":
        argv += ["--mobile-rotate"]
    elif egress in ("auto", "none", "warp"):
        argv += ["--proxy", egress]
    return argv


def _jobs_menu(jobs: "JobRegistry") -> None:
    """List/inspect/stop the background jobs started from the menu."""
    import kancahub
    while True:
        names = list(jobs.jobs)
        print(kancahub.col("bold", "\n  Background jobs:\n"))
        if not names:
            print(kancahub.col("dim", "  (none) — start one with menu option [2] gateway or [4] harvest\n"))
            return
        for i, n in enumerate(names, 1):
            state = kancahub.col("green", "● running") if jobs.alive(n) else kancahub.col("dim", "○ stopped")
            log = jobs.logs.get(n)
            print(f"   [{i}] {n:<10} {state}   log: {log}")
        print("\n   [l N] tail job N's log     [s N] stop job N     [a] stop all     [Enter] back")
        try:
            c = input("  jobs> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return
        if not c:
            return
        if c == "a":
            jobs.stop_all()
            continue
        if c.startswith(("l", "s")) and len(c) > 1:
            try:
                idx = int(c[1:].strip()) - 1
                name = names[idx]
            except (ValueError, IndexError):
                print(kancahub.col("yellow", "  pick a valid number"))
                continue
            if c[0] == "s":
                jobs.stop(name)
            else:
                log = jobs.logs.get(name)
                if log and log.exists():
                    print(kancahub.col("dim", f"  — tail of {log} —"))
                    print("\n".join(log.read_text(errors="replace").splitlines()[-25:]))
                else:
                    print(kancahub.col("dim", "  no log"))


def interactive_mode(p: argparse.ArgumentParser) -> int:
    """Beginner-friendly unified interactive menu when run without arguments."""
    import kancahub
    print(kancahub.get_ascii_banner())
    jobs = kancahub.JobRegistry()

    while True:
        print(kancahub.render_menu())
        running = jobs.running()
        if running:
            print(kancahub.col("green", f"  background jobs: {', '.join(running)}   (choose [j] to manage)"))
        print()

        try:
            choice = input(f" {kancahub.C['bold']}Select an option [0-{len(kancahub.UNIFIED_MENU)-1}, j, h, q]: {kancahub.C['reset']}").strip().lower()
        except (EOFError, KeyboardInterrupt):
            jobs.stop_all()
            print("\nExiting.")
            return 0

        if not choice or choice in ("q", "quit", "exit"):
            jobs.stop_all()
            return 0
        if choice == "h":
            p.print_help()
            return 0
        if choice == "j":
            kancahub._jobs_menu(jobs)
            continue
        if choice == "0":
            try:
                kancahub.run_end_to_end_flow(p)
            except Exception as e:
                print(kancahub.col("red", f"Error in end-to-end setup: {e}"))
            print()
            try:
                input(f" {kancahub.C['bold']}Press Enter to return to menu...{kancahub.C['reset']}")
            except (EOFError, KeyboardInterrupt):
                return 0
            print()
            continue

        if choice == "20":
            try:
                url = input(f" {kancahub.C['bold']}Target signup/login URL: {kancahub.C['reset']}").strip()
            except (EOFError, KeyboardInterrupt):
                continue
            if not url:
                continue
            dom = input(f" {kancahub.C['bold']}Mailbox [1=my.id, 2=biz.id, 3=litensi, 4=static, 5=real gmail]: {kancahub.C['reset']}").strip()
            if dom == "2":
                domain, mail = "kancalabs.biz.id", "auto"
            elif dom == "3":
                domain, mail = "kancalabs.my.id", "litensi"
            elif dom == "4":
                domain, mail = "kancalabs.my.id", "static"
            elif dom == "5":
                domain, mail = "kancalabs.my.id", "gmail"
            else:
                domain, mail = "kancalabs.my.id", "auto"
            cmd_args = ["autofarm", url, "--domain", domain, "--mail", mail]
            inj = input(f" {kancahub.C['bold']}Inject into 9Router? (y/N): {kancahub.C['reset']}").strip().lower()
            if inj in ("y", "yes"):
                cmd_args.append("--inject-9router")
            hl = input(f" {kancahub.C['bold']}Run headless? (y/N): {kancahub.C['reset']}").strip().lower()
            if hl in ("y", "yes"):
                cmd_args.append("--headless")
            print(kancahub.col("cyan", f"\n▶ Running: kancahub {' '.join(cmd_args)}\n"))
            try:
                kancahub.dispatch(p, p.parse_args(cmd_args))
            except Exception as e:  # noqa: BLE001
                print(kancahub.col("red", f"Error: {e}"))
            print()
            try:
                input(f" {kancahub.C['bold']}Press Enter to return to menu...{kancahub.C['reset']}")
            except (EOFError, KeyboardInterrupt):
                jobs.stop_all()
                return 0
            print()
            continue

        match = next((item for item in kancahub.UNIFIED_MENU if item[0] == choice), None)
        if not match:
            print(kancahub.col("yellow", f"  Please enter a valid option between 0 and {len(kancahub.UNIFIED_MENU)-1}, h, or q."))
            continue

        _key, _desc, cmd_str, cmd_args = match
        if not cmd_args:
            continue

        # Long-running / server-y items run in the BACKGROUND so the menu is never
        # stuck on PetaniProxy — the flow continues in the same CLI.
        if _key in kancahub.MENU_BACKGROUND:
            job = kancahub.MENU_BACKGROUND[_key]
            print(kancahub.col("cyan", f"\n▶ Starting in background: kancahub {cmd_str}\n"))
            jobs.start(job, [sys.executable, str(kancahub.SCRIPTS_DIR / "kancahub.py")] + kancahub.MENU_BACKGROUND_CMD.get(_key, cmd_args))
            print(kancahub.col("dim", "  the menu stays usable — choose [j] to see/stop jobs.\n"))
            continue

        # Farm items: ask for count/pace/egress IN THE CLI (all inside kancahub).
        if _key in kancahub.MENU_FARM_KEYS:
            print()
            cmd_args = kancahub._menu_ask_farm_options(_key, list(cmd_args))
            cmd_str = " ".join(cmd_args)

        print(kancahub.col("cyan", f"\n▶ Running: kancahub {cmd_str}\n"))
        try:
            kancahub.dispatch(p, p.parse_args(cmd_args))
        except Exception as e:
            print(kancahub.col("red", f"Error: {e}"))
        print()
        try:
            input(f" {kancahub.C['bold']}Press Enter to return to menu...{kancahub.C['reset']}")
        except (EOFError, KeyboardInterrupt):
            jobs.stop_all()
            return 0
        print()


def beginner_entry(p: argparse.ArgumentParser) -> int:
    """Show the beginner wizard; offer an escape to the advanced menu."""
    import kancahub
    kancahub.sys.path.insert(0, str(kancahub.AUTO_FREECF / "scripts"))
    try:
        from beginner import beginner_menu
    except Exception:
        return kancahub.interactive_mode(p)  # fall back to the classic menu

    # offer advanced escape on first screen
    print(kancahub.get_ascii_banner())
    print(kancahub.col("bold", "  New here? Just answer the questions.\n"))
    print(kancahub.col("dim", "  (advanced users: run `kancahub menu` for the full command list)\n"))
    try:
        return beginner_menu()
    except KeyboardInterrupt:
        print(kancahub.col("dim", "\n  Bye!"))
        return 0


def menu_entry(p: argparse.ArgumentParser) -> int:
    """The classic numbered command menu (for advanced users)."""
    import kancahub
    return kancahub.interactive_mode(p)
