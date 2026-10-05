#!/usr/bin/env python3
"""
KancaHub Beginner Mode — for people who don't know what any of this means.

Goal: a total beginner can double-click/run ONE command and get a result,
without learning flags, commands, or jargon.

Design principles:
  * Ask what they want in plain language ("Get free AI access" not "stack signup")
  * Only ask follow-up questions that matter, with sane defaults (press Enter)
  * Explain things inline in one sentence, no acronyms
  * After running, print a plain-English result ("✅ You now have 3 working keys")
  * Never show a raw command unless they ask (press 'v')

Entry: `kancahub` (no args) -> beginner wizard, or `kancahub beginner`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
AUTO_FREECF = HOME / "Auto-FreeCF"
SCRIPTS = AUTO_FREECF / "scripts"
VENV_PY = HOME / ".local" / "share" / "auto-freecf" / "venv" / "bin" / "python"
CAMOUFOX_PY = HOME / ".local" / "share" / "auto-freecf" / "camoufox-venv" / "bin" / "python"

C = {"reset": "\x1b[0m", "bold": "\x1b[1m", "dim": "\x1b[2m", "cyan": "\x1b[36m",
     "green": "\x1b[32m", "yellow": "\x1b[33m", "red": "\x1b[31m", "magenta": "\x1b[35m"}


def c(name: str, text: str) -> str:
    return f"{C.get(name, '')}{text}{C['reset']}"


def hr() -> None:
    print(c("dim", "─" * 64))


def ask(prompt: str, default: str = "") -> str:
    suffix = f" {c('dim', f'[Enter = {default}]')}" if default else ""
    try:
        v = input(f"  {c('bold', prompt)}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)
    return v or default


def yesno(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    v = ask(f"{prompt} ({d})").strip().lower()
    if not v:
        return default
    return v.startswith("y")


def run_script(script: str, args: list[str], *, python: str | None = None, cwd: Path | None = None) -> int:
    py = python or (str(VENV_PY) if VENV_PY.exists() else sys.executable)
    cmd = [py, str(SCRIPTS / script)] + args
    print(c("dim", f"\n  running: {' '.join(cmd)}\n"))
    try:
        return subprocess.call(cmd, cwd=str(cwd) if cwd else str(SCRIPTS))
    except KeyboardInterrupt:
        print(c("yellow", "\n  stopped."))
        return 130


# ═══════════════════════════════════════════════ main beginner menu

def beginner_menu() -> int:
    from beginner_help import (  # local module
        explain_what_is_this,
    )
    while True:
        hr()
        print(c("bold", "  What would you like to do?  ") + c("dim", "(type a number, or q to quit)\n"))
        print(f"   {c('green', '1')}  Get free AI access" + c("dim", "   (create Cloudflare Workers AI keys)"))
        print(f"   {c('green', '2')}  Check if everything is ready" + c("dim", "  (recommended first)"))
        print(f"   {c('green', '3')}  Turn my computer into a proxy" + c("dim", " (help signups not get blocked)"))
        print(f"   {c('green', '4')}  Get free TokenHarbor AI keys")
        print(f"   {c('green', '5')}  Create a GitHub student account")
        print(f"   {c('green', '6')}  Verify my student status (SheerID)")
        print(f"   {c('green', '7')}  Make teacher/student documents (PDF/PNG)")
        print(f"   {c('green', '8')}  Manage my Cloudflare accounts")
        print(f"   {c('green', '9')}  What is this? / Help")
        print(f"   {c('dim', 'q')}  Quit")
        print()
        choice = ask("Pick one", "2").lower()

        if choice in ("q", "quit", "exit"):
            print(c("dim", "  Bye!"))
            return 0
        if choice == "1":
            return wizard_cloudflare()
        if choice == "2":
            return wizard_check()
        if choice == "3":
            return wizard_proxy()
        if choice == "4":
            return wizard_tokenharbor()
        if choice == "5":
            return wizard_github()
        if choice == "6":
            return wizard_sheerid()
        if choice == "7":
            return wizard_documents()
        if choice == "8":
            return wizard_manage()
        if choice == "9":
            explain_what_is_this()
            continue
        print(c("yellow", "  Please type one of the numbers shown."))


# ═══════════════════════════════════════════════ wizards

def wizard_cloudflare() -> int:
    hr()
    print(c("bold", "  Get free AI access (Cloudflare Workers AI)\n"))
    print(c("dim", "  This creates a Cloudflare account and a free API key you can"))
    print(c("dim", "  use in AI apps. It runs in the background for a few minutes.\n"))

    n = ask("How many accounts/keys do you want", "1")
    try:
        n = max(1, int(n))
    except ValueError:
        n = 1

    print(c("dim", "\n  Tip: a 'clean IP' (VPN/proxy) helps avoid Cloudflare blocking."))
    use_warp = yesno("  Use Cloudflare WARP for a clean IP", True)
    inject = yesno("  Save the keys into 9Router (your AI gateway)", True)

    args = ["-n", str(n)]
    if use_warp:
        args.append("--warp")
    if not inject:
        args.append("--no-inject")

    print(c("cyan", f"\n  ▶ Starting. This can take a few minutes — please wait…\n"))
    rc = run_script("pipeline.py", args, cwd=AUTO_FREECF)

    if rc == 0:
        print(c("green", "\n  ✅ Done! You now have working Cloudflare AI keys."))
        print(c("dim", "     They were saved to your 'results' file and (if chosen) into 9Router."))
    else:
        print(c("yellow", f"\n  ⚠️  It didn't finish cleanly (exit {rc})."))
        print(c("dim", "     Most common cause: your internet IP is blocked by Cloudflare."))
        print(c("dim", "     Try option 3 (proxy) first, then run this again."))
    print()
    ask("Press Enter to go back")
    return 0


def wizard_check() -> int:
    hr()
    print(c("bold", "  Checking if everything is ready…\n"))
    rc = run_script("kancahub.py", ["doctor"])
    print()
    if rc == 0:
        print(c("green", "  ✅ Here's your setup. Lines with ❌ are things to fix."))
    else:
        print(c("yellow", "  ⚠️  Some checks failed — see the ❌ marks above."))
    ask("Press Enter to go back")
    return 0


def wizard_proxy() -> int:
    hr()
    print(c("bold", "  Turn on a proxy (so signups don't get blocked)\n"))
    print(c("dim", "  This finds working proxies and starts a local gateway on"))
    print(c("dim", "  http://127.0.0.1:8888 . Other tools use it automatically.\n"))

    how = ask("Find fresh free proxies (f) or use my own file (m)", "f").lower()
    if how.startswith("m"):
        pool = ask("Path to your proxy list file")
        if not pool or not Path(pool).exists():
            print(c("red", "  ✗ file not found; going back."))
            return 0
        print(c("cyan", "\n  ▶ Starting your proxy gateway (Ctrl-C to stop)…\n"))
        rc = run_script("proxy_gateway.py", ["--pool", pool, "--port", "8888"])
    else:
        print(c("cyan", "\n  ▶ Step 1: finding working proxies (a few seconds)…\n"))
        out = "/tmp/kancahub_pool.txt"
        rc = run_script("proxy_lib.py", ["harvest", "--target", "15", "--out-txt", out])
        if rc != 0 or not Path(out).exists():
            print(c("red", "  ✗ couldn't find usable proxies right now. Try again later."))
            ask("Press Enter to go back")
            return 0
        print(c("cyan", "\n  ▶ Step 2: starting your gateway on :8888 (Ctrl-C to stop)…\n"))
        run_script("proxy_gateway.py", ["--pool", out, "--port", "8888"])

    ask("\nPress Enter to go back")
    return 0


def wizard_tokenharbor() -> int:
    hr()
    print(c("bold", "  Get free TokenHarbor AI keys\n"))
    print(c("dim", "  Creates a TokenHarbor account and an API key, then (optionally)"))
    print(c("dim", "  saves it into 9Router.\n"))
    n = ask("How many keys", "1")
    inject = yesno("  Save keys into 9Router", True)
    print(c("cyan", "\n  ▶ Creating…\n"))
    print(c("dim", "  (Note: this needs working proxies. If it fails, run option 3 first.)\n"))
    rc = run_script("kancahub.py", ["thk", "batch", str(n)])
    if rc == 0 and inject:
        run_script("kancahub.py", ["thk", "inject"])
    print(c("green", "\n  ✅ Finished.") if rc == 0 else c("yellow", "\n  ⚠️  Didn't finish cleanly."))
    ask("Press Enter to go back")
    return 0


def wizard_github() -> int:
    hr()
    print(c("bold", "  Create a GitHub student account\n"))
    print(c("dim", "  Signs up a new GitHub account using your school email, then"))
    print(c("dim", "  helps you apply for the Student Pack.\n"))
    print(c("yellow", "  ⚠️  A human must finish the CAPTCHA and the school-ID step.\n"))
    idx = ask("Account number (1 for the first)", "1")
    use_proxy = yesno("  Use a clean proxy automatically", True)
    args = ["--index", idx]
    if use_proxy:
        args.append("--proxy")
        args.append("auto")
    print(c("cyan", "\n  ▶ Running the signup helper…\n"))
    rc = run_script("github_farm.py", ["--index", idx], python=str(CAMOUFOX_PY) if CAMOUFOX_PY.exists() else None)
    print(c("green", "\n  ✅ Finished.") if rc == 0 else c("yellow", "\n  ⚠️  Stopped — check the messages above."))
    ask("Press Enter to go back")
    return 0


def wizard_sheerid() -> int:
    hr()
    print(c("bold", "  Verify my student status (SheerID)\n"))
    print(c("dim", "  Finds the verification link in your school email and opens it.\n"))
    print(c("dim", "  Your school mailbox is: ") + c("cyan", os.environ.get("SCHOOL_EMAIL", "(set SCHOOL_EMAIL in .env)")))
    print()
    rc = run_script("sheerid_link_finder.py", ["--open"],
                    python=str(CAMOUFOX_PY) if CAMOUFOX_PY.exists() else None)
    print(c("green", "\n  ✅ Finished.") if rc == 0 else c("yellow", "\n  ⚠️  No link found — make sure your school login is set up."))
    ask("Press Enter to go back")
    return 0


def wizard_documents() -> int:
    hr()
    print(c("bold", "  Make teacher / student documents\n"))
    print(c("dim", "  Generates realistic ID cards, letters, or payslips.\n"))
    first = ask("First name", "John")
    last = ask("Last name", "Doe")
    country = ask("Country code (us, uk, indonesia, …)", "us")
    print(c("dim", "\n  (school/position default to sensible values)\n"))
    rc = run_script("kancahub.py", ["yowes", "make", "--country", country,
                                    "--first", first, "--last", last,
                                    "--school", "Norton Elementary"])
    if rc == 0:
        print(c("green", "\n  ✅ Documents saved to the yowes/output folder."))
    ask("Press Enter to go back")
    return 0


def wizard_manage() -> int:
    hr()
    print(c("bold", "  Manage my Cloudflare accounts\n"))
    print(f"   {c('green','1')}  Log in to an account I already have")
    print(f"   {c('green','2')}  Clean up dead keys")
    print(f"   {c('green','3')}  See how many keys I have")
    print(f"   {c('green','4')}  Back")
    print()
    ch = ask("Pick one", "3")
    if ch == "1":
        acct = ask("Your email:password (nothing is sent anywhere but Cloudflare)")
        if ":" in acct:
            run_script("kancahub.py", ["stack", "login", acct])
    elif ch == "2":
        run_script("kancahub.py", ["stack", "sync", "--prune"])
    elif ch == "3":
        run_script("kancahub.py", ["doctor"])
    ask("Press Enter to go back")
    return 0


if __name__ == "__main__":
    print(c("cyan", "\n  Welcome to KancaHub — Beginner Mode\n"))
    try:
        sys.exit(beginner_menu())
    except KeyboardInterrupt:
        print(c("dim", "\n  Bye!"))
        sys.exit(0)
