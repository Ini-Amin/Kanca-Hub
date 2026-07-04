#!/usr/bin/env python3
"""Batch runner: Process all workspace accounts through CF Workers AI grabber."""

import subprocess
import sys
import time
from pathlib import Path

ACCOUNTS_FILE = Path(__file__).parent / "accounts.txt"
SUCCESS = []
FAILED = []


def load_accounts():
    accounts = []
    for line in ACCOUNTS_FILE.read_text().strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        email, password = line.split(":", 1)
        accounts.append((email, password))
    return accounts


def run_grabber(email: str, password: str) -> bool:
    """Run bot.py for a single account."""
    print(f"\n{'='*60}")
    print(f"📧 Processing: {email}")
    print(f"{'='*60}")

    result = subprocess.run(
        [
            sys.executable, "bot.py",
            "--email", email,
            "--password", password,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=Path(__file__).parent,
    )

    print(result.stdout)
    if result.returncode != 0:
        print(f"❌ STDERR: {result.stderr[:500]}")

    return result.returncode == 0


def main():
    accounts = load_accounts()
    print(f"📋 Loaded {len(accounts)} accounts from {ACCOUNTS_FILE}")

    for i, (email, password) in enumerate(accounts, 1):
        print(f"\n[{i}/{len(accounts)}]")
        ok = run_grabber(email, password)

        if ok:
            SUCCESS.append(email)
        else:
            FAILED.append(email)

        # Jeda antar akun biar gak kena rate limit
        if i < len(accounts):
            print("⏳ Waiting 5s before next account...")
            time.sleep(5)

    print(f"\n{'='*60}")
    print(f"📊 BATCH COMPLETE")
    print(f"   ✅ Success: {len(SUCCESS)}")
    print(f"   ❌ Failed:  {len(FAILED)}")
    if SUCCESS:
        print(f"   Success: {', '.join(SUCCESS)}")
    if FAILED:
        print(f"   Failed:  {', '.join(FAILED)}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
