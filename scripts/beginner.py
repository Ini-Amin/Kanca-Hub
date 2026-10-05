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
        print(f"   {c('green', '6')}  Create Gmail accounts")
        print(f"   {c('green', '7')}  Use my xAI / Grok account" + c("dim", "  (add your xAI API key)"))
        print(f"   {c('green', '8')}  Make teacher/student documents (PDF/PNG)")
        print(f"   {c('green', '9')}  Manage my keys" + c("dim", " (Cloudflare, TokenHarbor, 9Router)"))
        print(f"   {c('green', '10')}  What is this? / Help")
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
            return wizard_gmail()
        if choice == "7":
            return wizard_xai()
        if choice == "8":
            return wizard_documents()
        if choice == "9":
            return wizard_manage()
        if choice == "10":
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
    print(c("dim", "  Pick a mode and KancaHub starts it for you. Other tools then use it"))
    print(c("dim", "  automatically at http://127.0.0.1:8888 .\n"))
    print(c("dim", "  Not sure? Choose 1 (WARP) — it's free and safe.\n"))
    run_script("kancahub.py", ["proxy", "start"])
    print()
    print(c("cyan", "  Tip: run  ") + c("bold", "kancahub proxy verify") + c("cyan", "  to PROVE your IP is hidden."))
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
    print(c("dim", "  It uses raymondi+gh<N>@binus.ac.id, so each number = a new"))
    print(c("dim", "  address that still lands in your one school inbox.\n"))
    idx = ask("Account number (1 = raymondi+gh1@binus.ac.id)", "1")
    try:
        int(idx)
    except ValueError:
        print(c("red", "\n  ✗ Please enter a NUMBER (e.g. 1)."))
        ask("Press Enter to go back")
        return 0
    use_proxy = yesno("  Use a clean proxy automatically", True)
    py = str(CAMOUFOX_PY) if CAMOUFOX_PY.exists() else None
    print(c("cyan", "\n  ▶ Running the signup helper…\n"))

    import subprocess
    cmd = [py or sys.executable, str(SCRIPTS / "github_farm.py"), "--index", str(idx)]
    if not use_proxy:
        cmd.append("--no-proxy")
    print(c("dim", f"  running: {' '.join(cmd)}\n"))
    try:
        rc = subprocess.call(cmd, cwd=str(AUTO_FREECF))
    except KeyboardInterrupt:
        rc = 130

    print(c("green", "\n  ✅ Finished.") if rc == 0 else c("yellow", "\n  ⚠️  Stopped — check the messages above."))
    ask("Press Enter to go back")
    return 0


def wizard_gmail() -> int:
    hr()
    print(c("bold", "  Create Gmail accounts\n"))
    print(c("dim", "  Automates Gmail signup in the background and saves the email +"))
    print(c("dim", "  password it created.\n"))
    print(c("yellow", "  ⚠️  Google may ask for a phone number / CAPTCHA — a human must"))
    print(c("yellow", "     finish that step when it appears.\n"))
    n = ask("How many accounts", "1")
    headless = yesno("  Run without showing the browser window", False)
    args = ["farm", "--count", n]
    if headless:
        args.append("--headless")
    print(c("cyan", "\n  ▶ Starting…\n"))
    rc = run_script("kancahub.py", ["gmail"] + args)
    if rc == 0:
        print(c("green", "\n  ✅ Finished — check github_accounts.json / the gmail output file."))
    else:
        print(c("yellow", "\n  ⚠️  Stopped early — check the messages above."))
    ask("Press Enter to go back")
    return 0


def wizard_xai() -> int:
    hr()
    print(c("bold", "  Use my xAI / Grok account\n"))
    print(c("dim", "  xAI (the company behind Grok) gives API keys at console.x.ai."))
    print(c("dim", "  That page needs you to LOG IN — it can't be auto-farmed, so this"))
    print(c("dim", "  just saves your key for your apps to use.\n"))
    print(f"   {c('cyan', 'Get a key:')} {c('bold', 'https://console.x.ai')}  -> log in -> API Keys -> Create\n")
    key = ask("Paste your xAI API key (or Enter to skip)")
    if key:
        _save_xai_key(key)
    else:
        print(c("dim", "  Nothing saved."))
    ask("Press Enter to go back")
    return 0


def _save_xai_key(key: str) -> None:
    """Save the xAI key into the env file and offer to inject into 9Router."""
    envp = HOME / ".config" / "auto-freecf" / ".env"
    try:
        txt = envp.read_text()
        if "XAI_API_KEY=" in txt:
            import re
            txt = re.sub(r"XAI_API_KEY=.*", f"XAI_API_KEY={key}", txt)
        else:
            txt = txt.rstrip() + f"\n\n# --- xAI / Grok ---\nXAI_API_KEY={key}\n"
        envp.write_text(txt)
        os.chmod(envp, 0o600)
        print(c("green", f"\n  ✅ Saved your xAI key to {envp} (kept private)."))
        if yesno("  Also add it to 9Router so your apps can use Grok", True):
            V = str(VENV_PY) if VENV_PY.exists() else sys.executable
            code = (
                "import sqlite3,os,json,uuid;"
                "import datetime as dt;"
                f"key={key!r};"
                "db=os.path.expanduser('~/.9router/db/data.sqlite');"
                "con=sqlite3.connect(db);"
                "ts=dt.datetime.now(dt.timezone.utc).isoformat();"
                "node=None;"
                "cur=con.cursor();"
                "rows=list(cur.execute(\"SELECT id,data FROM providerNodes\"));"
                "import sys;"
                "print('xAI saved. To attach to 9Router, add it under the xai provider in the 9Router UI.');"
            )
            # Best-effort: 9Router's xai provider is OAuth-only, so we just inform.
            print(c("dim", "\n  Note: 9Router's built-in 'xai' entry is login-based."))
            print(c("dim", "  The key is saved for any app that reads XAI_API_KEY."))
    except Exception as e:  # noqa: BLE001
        print(c("red", f"  ✗ could not save: {e}"))


def wizard_manage() -> int:
    hr()
    print(c("bold", "  Manage my keys\n"))
    print(f"   {c('green','1')}  See how many keys I have (health check)")
    print(f"   {c('green','2')}  Clean up dead keys")
    print(f"   {c('green','3')}  Log in to a Cloudflare account I already have")
    print(f"   {c('green','4')}  Show TokenHarbor keys + sync")
    print(f"   {c('green','5')}  Back")
    print()
    ch = ask("Pick one", "1")
    if ch == "1":
        run_script("kancahub.py", ["doctor"])
    elif ch == "2":
        run_script("kancahub.py", ["stack", "sync", "--prune"])
    elif ch == "3":
        acct = ask("Your email:password (typed locally, sent only to Cloudflare)")
        if ":" in acct:
            run_script("kancahub.py", ["stack", "login", acct])
    elif ch == "4":
        run_script("kancahub.py", ["thk", "sync"])
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


if __name__ == "__main__":
    print(c("cyan", "\n  Welcome to KancaHub — Beginner Mode\n"))
    try:
        sys.exit(beginner_menu())
    except KeyboardInterrupt:
        print(c("dim", "\n  Bye!"))
        sys.exit(0)
