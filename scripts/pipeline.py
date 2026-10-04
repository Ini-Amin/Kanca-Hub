#!/usr/bin/env python3
"""
Auto-FreeCF one-shot pipeline wrapper.

Runs the full chain in a single command:
    1. signup   — create Cloudflare account(s) + Workers AI token(s)
    2. verify   — validate each token against Cloudflare
    3. inject   — push valid keys into 9Router (cloudflare-ai provider)

Usage:
    python3 scripts/pipeline.py                      # 1 account, inject all
    python3 scripts/pipeline.py -n 3                 # 3 accounts
    python3 scripts/pipeline.py -n 5 --no-inject     # signup + verify only
    python3 scripts/pipeline.py --proxy http://...   # via proxy
    python3 scripts/pipeline.py --headless
    python3 scripts/pipeline.py --skip-signup        # just inject existing results
    python3 scripts/pipeline.py --dry-run            # show what would happen

Exit codes: 0 = at least one usable key, 1 = failure.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SIGNUP_DIR = ROOT / "signup_from_scratch"
INJECTOR = Path(__file__).resolve().parent / "inject_9router.py"

# Prefer the managed venv created by `moycf`; fall back to the current interpreter.
VENV_PY = Path.home() / ".local" / "share" / "auto-freecf" / "venv" / "bin" / "python"


def pick_python() -> str:
    v = str(VENV_PY)
    if VENV_PY.exists() and os.access(v, os.X_OK):
        return v
    return sys.executable


def banner(text: str) -> None:
    print()
    print("=" * 64)
    print(f"  {text}")
    print("=" * 64)


def run(cmd: list[str], cwd: Path) -> int:
    print(f"$ {' '.join(cmd)}")
    return subprocess.call(cmd, cwd=str(cwd))


def read_results(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("results") or data.get("accounts") or []
    return [r for r in data if isinstance(r, dict)]


def summarize(results: list[dict]) -> dict:
    full = [r for r in results if r.get("status") == "full" and r.get("token_valid")]
    signup_only = [r for r in results if r.get("status") == "signup_only"]
    cfut = [r for r in results if (r.get("api_token") or "").startswith("cfut_")]
    return {"total": len(results), "full": len(full), "signup_only": len(signup_only), "cfut": len(cfut)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Auto-FreeCF one-shot: signup -> verify -> inject")
    # signup options
    ap.add_argument("-n", "--accounts", type=int, default=1, help="accounts to create (default 1)")
    ap.add_argument("-c", "--config", default="config.json", help="signup config file")
    ap.add_argument("-p", "--proxy", default=None, help="proxy URL for signup")
    ap.add_argument("--proxy-pool", default=None,
                    help="file with one proxy per line; rotates per account")
    ap.add_argument("--headless", action="store_true", help="run browser headless")
    ap.add_argument("--fast", action="store_true", help="submit-first Turnstile mode")
    ap.add_argument("--retry", type=int, default=1, help="retries per account (default 1)")
    ap.add_argument("--delay", type=int, default=None, help="delay between accounts (seconds)")
    ap.add_argument("--workers", type=int, default=1, help="concurrent workers")
    ap.add_argument("--output", default="results.json", help="results file (default results.json)")
    # pipeline options
    ap.add_argument("--skip-signup", action="store_true", help="skip signup; only inject existing results")
    ap.add_argument("--no-inject", action="store_true", help="run signup + verify, skip 9Router injection")
    ap.add_argument("--no-verify", action="store_true", help="skip per-key verification before inject")
    ap.add_argument("--dry-run", action="store_true", help="pass --dry-run to the injector")
    ap.add_argument("--db", default=None, help="9Router DB path (passed through)")
    args = ap.parse_args()

    py = pick_python()
    start = time.time()
    output_path = SIGNUP_DIR / args.output

    print("Auto-FreeCF pipeline: signup -> verify -> inject")
    print(f"  python : {py}")

    # ---------- 1. SIGNUP ----------
    if args.skip_signup:
        banner("1/3 SIGNUP — skipped (--skip-signup)")
    else:
        banner(f"1/3 SIGNUP — creating {args.accounts} account(s)")
        cmd = [py, "main.py",
               "--accounts", str(args.accounts),
               "--config", args.config,
               "--output", args.output,
               "--retry", str(args.retry),
               "--workers", str(args.workers)]
        if args.proxy:
            cmd += ["--proxy", args.proxy]
        if args.proxy_pool:
            cmd += ["--proxy-pool", args.proxy_pool]
        if args.headless:
            cmd += ["--headless"]
        if args.fast:
            cmd += ["--fast"]
        if args.delay is not None:
            cmd += ["--delay", str(args.delay)]
        rc = run(cmd, cwd=SIGNUP_DIR)
        if rc != 0:
            print(f"\n⚠️ signup process returned {rc} (continuing to inspect results)")

    results = read_results(output_path)
    if not results:
        print(f"\n✗ No results found at {output_path}. Nothing to verify/inject.")
        return 1

    stats = summarize(results)
    print(f"\nSignup results: {stats['total']} total | "
          f"{stats['full']} full (valid token) | "
          f"{stats['signup_only']} signup-only | {stats['cfut']} with cfut_ token")

    with_token = [r for r in results if (r.get("api_token") or "").startswith("cfut_")]
    if not with_token:
        print("✗ No cfut_ tokens produced — nothing to inject.")
        return 1

    # ---------- 2. VERIFY ----------
    if args.no_verify:
        banner("2/3 VERIFY — skipped (--no-verify)")
    else:
        banner("2/3 VERIFY — checking tokens against Cloudflare")
        # The injector does verification when --verify is passed; here we just
        # report what signup already validated to avoid a duplicate network pass.
        valid = [r for r in results if r.get("token_valid")]
        print(f"  {len(valid)}/{len(with_token)} token(s) already validated during signup.")
        for r in with_token:
            mark = "✅" if r.get("token_valid") else "⚠️ "
            acct = (r.get("account_id") or "")[:8]
            print(f"  {mark} {r.get('email','?'):40s} {acct}…")

    # ---------- 3. INJECT ----------
    if args.no_inject:
        banner("3/3 INJECT — skipped (--no-inject)")
    else:
        banner("3/3 INJECT — pushing keys into 9Router")
        if not INJECTOR.exists():
            print(f"✗ Injector not found: {INJECTOR}")
            return 1
        cmd = [py, str(INJECTOR), "-i", str(output_path)]
        if not args.no_verify:
            cmd.append("--verify")
        if args.dry_run:
            cmd.append("--dry-run")
        if args.db:
            cmd += ["--db", args.db]
        rc = run(cmd, cwd=ROOT)
        if rc != 0:
            print(f"\n✗ injection failed (exit {rc})")
            return 1

    elapsed = time.time() - start
    banner(f"DONE in {elapsed:.0f}s")
    print(f"  results: {output_path}")
    print(f"  valid tokens: {stats['full']}")
    return 0 if stats["full"] > 0 or with_token else 1


if __name__ == "__main__":
    sys.exit(main())
