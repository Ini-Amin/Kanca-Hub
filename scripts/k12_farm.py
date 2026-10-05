#!/usr/bin/env python3
"""
KancaHub K-12 farm — batch ChatGPT teacher-account creation + 9Router injection.

Loops the K-12 flow (auto_k12_flow) N times with pacing, captures each ChatGPT
session, and injects them into 9Router's `codex` provider. Keeps retrying until
the target number of *usable* accounts is reached (or max attempts hit).

Design notes (why this shape):
  * K-12 accounts are ChatGPT web/OAuth accounts -> they land in 9Router as
    `codex` connections (see chatgpt_9router.py), not `openai` API keys.
  * The flow already writes k12_sessions.json per run; this loop simply keeps
    going and injects at the end (and incrementally, so a crash isn't fatal).
  * Each iteration is a fresh browser + fresh temp.tf mailbox.

Usage:
    python3 k12_farm.py -n 5                      # store 5 accounts
    python3 k12_farm.py -n 5 --headless           # (flow is headful-only)
    python3 k12_farm.py -n 5 --pace 30 --max-attempts 12
    python3 k12_farm.py -n 5 --no-inject
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
AUTO_FREECF = HOME / "Auto-FreeCF"
K12_DIR = HOME / "petani-proxy" / "Farm-Acc-ChatGPT-K-12-Teachers" / "PyRuntime_64"
FLOW = AUTO_FREECF / "scripts" / "auto_k12_flow_kancahub.py"
INJECT = AUTO_FREECF / "scripts" / "chatgpt_9router.py"
SESSIONS = K12_DIR / "k12_sessions.json"
CREATED = K12_DIR / "created_k12_accounts.txt"
VENV_PY = HOME / ".local" / "share" / "auto-freecf" / "venv" / "bin" / "python"

C = {"g": "\x1b[32m", "y": "\x1b[33m", "r": "\x1b[31m", "c": "\x1b[36m",
     "d": "\x1b[2m", "b": "\x1b[1m", "x": "\x1b[0m"}


def col(k: str, s: str) -> str:
    return f"{C.get(k, '')}{s}{C['x']}"


def pick_python() -> str:
    return str(VENV_PY) if VENV_PY.exists() else sys.executable


def count_sessions() -> int:
    if not SESSIONS.exists():
        return 0
    try:
        data = json.loads(SESSIONS.read_text())
        return sum(1 for s in data if isinstance(s, dict) and s.get("accessToken"))
    except Exception:
        return 0


def count_accounts() -> int:
    if not CREATED.exists():
        return 0
    return sum(1 for l in CREATED.read_text().splitlines() if l.strip())


def run_once(py: str, timeout: int) -> tuple[bool, str]:
    """Run one K-12 flow iteration. Returns (produced_session, tail)."""
    before = count_sessions()
    env_note = "running flow…"
    print(col("d", f"  $ {py} {FLOW}  (cwd={K12_DIR})"))
    try:
        p = subprocess.run([py, str(FLOW)], cwd=str(K12_DIR), capture_output=True,
                           text=True, timeout=timeout)
        tail = (p.stdout or "")[-1200:]
    except subprocess.TimeoutExpired:
        return False, "timeout"
    after = count_sessions()
    return after > before, tail


def main() -> int:
    ap = argparse.ArgumentParser(description="K-12 farm: loop flow -> store accounts -> inject 9Router")
    ap.add_argument("-n", "--target", type=int, default=5, help="accounts to store (default 5)")
    ap.add_argument("--max-attempts", type=int, default=None, help="max flow runs (default 3x target)")
    ap.add_argument("--pace", type=int, default=45, help="seconds between runs")
    ap.add_argument("--timeout", type=int, default=420, help="per-run timeout (s)")
    ap.add_argument("--no-inject", action="store_true", help="skip 9Router injection")
    ap.add_argument("--inject-every", type=int, default=1,
                    help="inject after every N successful accounts")
    a = ap.parse_args()

    py = pick_python()
    max_attempts = a.max_attempts or a.target * 3
    if not FLOW.exists():
        print(col("r", f"✗ flow not found: {FLOW}"))
        return 1

    print(col("b", f"\nK-12 farm — target {a.target} accounts (max {max_attempts} attempts, pace {a.pace}s)"))
    print(f"  sessions: {SESSIONS}")
    print(f"  creds   : {CREATED}")
    start_have = count_sessions()
    print(f"  existing sessions: {start_have}\n")

    ok = 0
    since_inject = 0
    for attempt in range(1, max_attempts + 1):
        have = count_sessions()
        if have >= a.target:
            break
        print(col("c", f"── attempt {attempt}/{max_attempts}  (have {have}/{a.target}) ──"))
        produced, tail = run_once(py, a.timeout)
        if produced:
            ok += 1
            since_inject += 1
            print(col("g", f"  ✅ session captured ({count_sessions()} total)"))
            if not a.no_inject and since_inject >= a.inject_every:
                subprocess.run([py, str(INJECT), "inject", "--session", str(SESSIONS)],
                               cwd=str(AUTO_FREECF))
                since_inject = 0
        else:
            print(col("y", f"  ✗ no session this run ({tail[:160].strip() or 'no output'})"))
        if count_sessions() < a.target and attempt < max_attempts:
            print(col("d", f"  sleeping {a.pace}s…"))
            time.sleep(a.pace)

    final = count_sessions()
    print(col("b", f"\nFARM DONE — {final} session(s) stored, {count_accounts()} credential line(s)."))
    if not a.no_inject and final > start_have:
        print(col("c", "Final 9Router inject:"))
        subprocess.run([py, str(INJECT), "inject", "--session", str(SESSIONS)], cwd=str(AUTO_FREECF))
        subprocess.run([py, str(INJECT), "sync"], cwd=str(AUTO_FREECF))
    return 0 if final >= a.target else 2


if __name__ == "__main__":
    sys.exit(main())
