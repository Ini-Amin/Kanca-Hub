#!/usr/bin/env python3
"""scripts/facebook_warmer.py — Facebook Account Seasoning, Warming & Profiling.

Automates the account-seasoning lifecycle for farmed Facebook identities:
  1. Computes account age (days since creation).
  2. Inspects session health (healthy feed vs checkpoint vs logged-out).
  3. Uploads persona-matched avatar if missing.
  4. Simulates realistic human browsing (reading feed, random scrolls, pauses).
  5. Refreshes and persists session cookies atomically.
  6. Tags accounts >= 3 days old with high trust as 'ready_for_muse'.

Usage:
  python3 scripts/facebook_warmer.py --status
  python3 scripts/facebook_warmer.py --email satriakencang81@kancalabs.my.id
  python3 scripts/facebook_warmer.py --all --duration 60
  python3 scripts/facebook_warmer.py --all --headed
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import random
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

HOME = Path.home()
LEDGER_PATH = HOME / ".config/auto-freecf/facebook_accounts.json"
SHOT_DIR = HOME / ".config/auto-freecf/facebook_warmer_shots"

# Display config for headed mode
if not os.environ.get("XAUTHORITY"):
    xauth = list(Path("/run/user/1000").glob("xauth_*"))
    if xauth:
        os.environ["XAUTHORITY"] = str(xauth[0])
if not os.environ.get("DISPLAY"):
    os.environ["DISPLAY"] = ":0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_ledger() -> list[dict]:
    if not LEDGER_PATH.exists():
        return []
    try:
        return json.loads(LEDGER_PATH.read_text())
    except Exception:
        return []


def _save_ledger(accounts: list[dict]) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_file = LEDGER_PATH.with_suffix(".lock")
    with open(lock_file, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        tmp = LEDGER_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(accounts, indent=2))
        tmp.replace(LEDGER_PATH)
        fcntl.flock(lf, fcntl.LOCK_UN)


def get_account_age_days(created_at_str: str | None) -> float:
    if not created_at_str:
        return 0.0
    try:
        dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return round((now - dt).total_seconds() / 86400.0, 2)
    except Exception:
        return 0.0


def fetch_matching_avatar(gender: str = "female", out_path: str = "/tmp/warmer_avatar.jpg") -> str | None:
    """Download a natural casual portrait matching the persona."""
    try:
        url = f"https://randomuser.me/api/?nat=us&gender={gender}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
            pic_url = data["results"][0]["picture"]["large"]

        req_pic = urllib.request.Request(pic_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req_pic, timeout=10) as r_pic:
            Path(out_path).write_bytes(r_pic.read())
            return out_path
    except Exception:
        return None


def print_status_table() -> None:
    accounts = _read_ledger()
    print("\n" + "=" * 90)
    print(f"{'#':<3} {'EMAIL':<32} {'NAME':<18} {'AGE (DAYS)':<11} {'HEALTH':<12} {'MUSE READY'}")
    print("-" * 90)
    for idx, a in enumerate(accounts):
        age = get_account_age_days(a.get("created_at"))
        status = a.get("health_status") or ("checkpoint" if a.get("checkpoint") else "untested")
        ready = "✓ READY" if a.get("ready_for_muse") or (age >= 3.0 and status == "healthy") else "Waiting"
        email = a.get("email", "")[:30]
        name = a.get("name", "")[:16]
        print(f"[{idx+1:02d}] {email:<32} {name:<18} {age:<11.1f} {status:<12} {ready}")
    print("=" * 90 + "\n")


def warm_account(account: dict, *, duration: int = 60, headed: bool = False, update_avatar: bool = True) -> dict:
    from camoufox.sync_api import Camoufox

    email = account.get("email")
    name = account.get("name", "User")
    age = get_account_age_days(account.get("created_at"))
    print("\n" + "-" * 70)
    print(f"[*] Warming Account: {name} <{email}> (Age: {age:.1f} days)")

    raw_cookies = account.get("cookies", {})
    if not raw_cookies:
        print("    [!] No cookies stored for this account. Skipping.")
        account["health_status"] = "no_cookies"
        return account

    fb_cookies = []
    for k, v in raw_cookies.items():
        fb_cookies.append({
            "name": k, "value": v, "domain": ".facebook.com", "path": "/", "secure": True
        })

    kwargs = {
        "headless": not headed,
        "humanize": True,
        "locale": "en-US",
        "geoip": False,
    }

    SHOT_DIR.mkdir(parents=True, exist_ok=True)

    with Camoufox(**kwargs) as browser:
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 900},
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"}
        )
        ctx.add_cookies(fb_cookies)
        page = ctx.new_page()

        # Step 1: Open Facebook Homepage
        print("    [1/4] Navigating to https://www.facebook.com/...")
        try:
            page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=40000)
            time.sleep(4)
        except Exception as e:
            print(f"    [!] Navigation error: {e}")
            account["health_status"] = "nav_error"
            return account

        current_url = page.url
        body = page.locator("body").inner_text()[:600]

        # Step 2: Health Check
        if "checkpoint" in current_url:
            print(f"    [!] Checkpoint detected: {current_url[:60]}")
            if "video selfie" in body.lower() or "selfie" in body.lower():
                account["health_status"] = "selfie_checkpoint"
            elif "confirm that you're human" in body.lower() or "audio" in body.lower():
                account["health_status"] = "captcha_checkpoint"
            else:
                account["health_status"] = "checkpoint"
            shot_p = SHOT_DIR / f"checkpoint-{email.split('@')[0]}.png"
            page.screenshot(path=str(shot_p))
            return account

        if "login" in current_url and "c_user" not in body:
            print("    [!] Sesi kedaluwarsa / logged out.")
            account["health_status"] = "logged_out"
            return account

        print("    [✓] Feed loaded successfully! Account is HEALTHY.")
        account["health_status"] = "healthy"

        # Step 3: Natural News Feed Browsing
        steps = max(3, duration // 10)
        print(f"    [2/4] Simulating natural feed interaction ({duration}s, ~{steps} scrolls)...")
        for s in range(steps):
            scroll_dist = random.randint(350, 650)
            page.mouse.wheel(0, scroll_dist)
            pause = random.uniform(2.5, 4.5)
            time.sleep(pause)

            # 25% chance of reverse scroll (reading earlier post)
            if random.random() < 0.25:
                page.mouse.wheel(0, -random.randint(100, 250))
                time.sleep(random.uniform(1.5, 2.5))

            if (s + 1) % 3 == 0:
                print(f"      ...browsed post {s+1}/{steps}")

        shot_feed = SHOT_DIR / f"feed-{email.split('@')[0]}.png"
        page.screenshot(path=str(shot_feed))

        # Step 4: Profile Page check & avatar upload if missing
        if update_avatar and not account.get("avatar_set"):
            print("    [3/4] Visiting profile (https://www.facebook.com/me)...")
            try:
                page.goto("https://www.facebook.com/me", wait_until="domcontentloaded", timeout=35000)
                time.sleep(4)

                # Check if default avatar is present
                camera_btn = page.locator('[aria-label*="profile picture" i], [aria-label*="Update profile picture" i]').first
                if camera_btn.is_visible():
                    gender = "female" if any(f in name.lower() for f in ["sarah", "emily", "jessica", "hannah", "pooja", "yasmine"]) else "male"
                    avatar_file = fetch_matching_avatar(gender=gender, out_path=f"/tmp/avatar_{email.split('@')[0]}.jpg")
                    if avatar_file and Path(avatar_file).exists():
                        camera_btn.click()
                        time.sleep(2)
                        file_inp = page.locator('input[type="file"]').first
                        if file_inp.count() > 0:
                            file_inp.set_input_files(avatar_file)
                            time.sleep(3)
                            save_btn = page.locator('button:has-text("Save"), [role="button"]:has-text("Save")').first
                            if save_btn.is_visible():
                                save_btn.click()
                                time.sleep(5)
                                account["avatar_set"] = True
                                print("    [✓] Uploaded and set persona profile picture!")
            except Exception as e:
                print(f"    [i] Profile visit skipped: {e}")

        # Step 5: Refresh and persist session cookies
        print("    [4/4] Refreshing and persisting active session cookies...")
        new_cookies = {c["name"]: c["value"] for c in ctx.cookies() if "facebook.com" in (c.get("domain") or "")}
        account["cookies"] = {**account.get("cookies", {}), **new_cookies}
        account["cookie_header"] = "; ".join(f"{k}={v}" for k, v in account["cookies"].items())
        account["warm_count"] = account.get("warm_count", 0) + 1
        account["last_warmed_at"] = _now()

        # Tag for Muse readiness
        if age >= 3.0 and account["health_status"] == "healthy":
            account["ready_for_muse"] = True
            print("    [🌟] ACCOUNT IS SEASONED (>=3 DAYS) & HEALTHY -> MARKED 'READY FOR MUSE'!")
        else:
            days_left = max(0.0, 3.0 - age)
            print(f"    [i] Seasoning progress: {age:.1f}/3.0 days (need {days_left:.1f} more days).")

    return account


def main() -> None:
    parser = argparse.ArgumentParser(description="Facebook Account Seasoning & Warming Runner")
    parser.add_argument("--status", action="store_true", help="Print status and age table of all accounts")
    parser.add_argument("--email", default="", help="Warm a specific account by email")
    parser.add_argument("--all", action="store_true", help="Warm all accounts in ledger sequentially")
    parser.add_argument("--duration", type=int, default=45, help="Browsing duration per account in seconds (default: 45)")
    parser.add_argument("--headed", action="store_true", help="Run with visible browser window")
    parser.add_argument("--no-avatar", action="store_true", help="Skip avatar check/upload")
    args = parser.parse_args()

    if args.status or (not args.email and not args.all):
        print_status_table()
        if not args.email and not args.all:
            return

    accounts = _read_ledger()
    if not accounts:
        sys.exit(f"No accounts found in {LEDGER_PATH}")

    if args.email:
        target = next((a for a in accounts if a.get("email") == args.email), None)
        if not target:
            sys.exit(f"Account {args.email} not found in ledger")
        updated = warm_account(target, duration=args.duration, headed=args.headed, update_avatar=not args.no_avatar)
        for i, a in enumerate(accounts):
            if a.get("email") == args.email:
                accounts[i] = updated
                break
        _save_ledger(accounts)
        print(f"\n[✓] Finished warming {args.email}. Ledger updated.")
        return

    if args.all:
        print(f"\n[*] Starting batch warming cycle for {len(accounts)} accounts...")
        for idx, acc in enumerate(accounts):
            print(f"\n=== Progress: {idx+1}/{len(accounts)} ===")
            try:
                updated = warm_account(acc, duration=args.duration, headed=args.headed, update_avatar=not args.no_avatar)
                accounts[idx] = updated
                _save_ledger(accounts)
            except Exception as e:
                print(f"[!] Error warming {acc.get('email')}: {e}")

            if idx + 1 < len(accounts):
                gap = random.randint(4, 8)
                print(f"    Pausing {gap}s before next account...")
                time.sleep(gap)

        print("\n[✓] BATCH WARMING CYCLE COMPLETED!")
        print_status_table()


if __name__ == "__main__":
    main()
