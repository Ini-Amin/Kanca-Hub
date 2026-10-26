#!/usr/bin/env python3
"""scripts/autoclaw_farm.py — AutoClaw claim farm, 1 account per run (gentle).

Recipe (pinned in AGENTS.md):
  1. Temporary xdg-open wrapper in PATH captures the OAuth URL from shell.openExternal.
  2. Launch patched AppRun with CDP :9444 on X11 DISPLAY=:0.
  3. CDP click: "Continue with Zai".
  4. App pops "Security verification" Aliyun window. xdotool clicks (570, 423).
  5. App calls shell.openExternal(chat.z.ai/api/oauth/authorize?...) -> caught by wrapper.
  6. Drive the z.ai half via Camoufox (email + Aliyun slider + consent screen).
  7. App loopback :18432 receives the code, exchanges tokens, writes vault.
  8. AutoClawClient triggers claim_daily_credits() -> 40M tokens in campaign wallet.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from autoclaw_client import AutoClawClient, load_app_credentials  # noqa: E402
from consent_hard import accept_consent, signin_zai_email  # noqa: E402

APPRUN = Path("/home/amen/Auto-FreeCF-vendor-refs/artifacts/autoclaw/squashfs-root/AppRun")
DEFAULT_PORT = 9444


def find_display_and_xauth() -> tuple[str, str]:
    disp = os.environ.get("DISPLAY") or ":0"
    xa = os.environ.get("XAUTHORITY")
    if xa and Path(xa).exists():
        return disp, xa
    cand = list(Path("/run/user/1000").glob("xauth_*"))
    if cand:
        return disp, str(sorted(cand)[-1])
    home_xa = Path.home() / ".Xauthority"
    return disp, str(home_xa) if home_xa.exists() else ""


def click_security_verification_window(log=print) -> bool:
    """Find the Security verification popup and click the Aliyun checkbox with xdotool."""
    try:
        wids = subprocess.check_output(["xdotool", "search", "--onlyvisible", "--name", ""],
                                       text=True).splitlines()
    except Exception as e:
        log(f"  xdotool search error: {e}")
        return False

    target_wid = None
    for wid in wids:
        try:
            name = subprocess.check_output(["xdotool", "getwindowname", wid], text=True).strip()
            if any(k in name.lower() for k in ("security verification", "安全验证", "verification")):
                target_wid = wid
                log(f"  found security window: wid={wid} name={name!r}")
                break
        except Exception:
            continue

    if not target_wid:
        log("  no security verification window found by name")
        return False

    # Pinned in AGENTS.md: click at (570, 423) relative to the security window
    try:
        subprocess.run(["xdotool", "windowactivate", "--sync", target_wid], check=True, timeout=5)
        time.sleep(0.5)
        subprocess.run(["xdotool", "mousemove", "--window", target_wid, "570", "423",
                        "click", "1"], check=True, timeout=5)
        log("  xdotool clicked Aliyun checkbox at (570, 423)")
        return True
    except Exception as e:
        log(f"  xdotool click error: {e}")
        return False


def pick_account_from_farm() -> tuple[str, str] | None:
    """Return an (email, password) candidate from accounts.txt or accounts.json."""
    for p in (ROOT / "accounts.txt", ROOT / "data" / "accounts.txt",
              Path.home() / ".zcode-bridge" / "accounts.txt"):
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                parts = line.split(":", 1)
                if "@" in parts[0] and len(parts[1]) >= 6:
                    return parts[0].strip(), parts[1].strip()
    return None


async def drive_cdp_continue_zai(cdp_port: int, log=print) -> bool:
    """Connect to AppRun via Playwright CDP and click 'Continue with Zai'."""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        try:
            browser = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
        except Exception as e:
            log(f"  CDP connect failed: {e}")
            return False

        clicked = False
        for ctx in browser.contexts:
            for page in ctx.pages:
                try:
                    title = await page.title()
                    log(f"  page: {title} ({page.url})")
                    # Try clicking "Continue with Zai"
                    loc = page.get_by_text("Continue with Zai", exact=False).first
                    if await loc.count() and await loc.is_visible():
                        await loc.click()
                        log("  clicked 'Continue with Zai' via CDP")
                        clicked = True
                        break
                    # Fallback selectors for Chinese UI
                    loc_zh = page.get_by_text("使用 Z.ai 登录", exact=False).first
                    if await loc_zh.count() and await loc_zh.is_visible():
                        await loc_zh.click()
                        log("  clicked '使用 Z.ai 登录' via CDP")
                        clicked = True
                        break
                except Exception:
                    continue
            if clicked:
                break
        await browser.close()
        return clicked


async def drive_zai_oauth_login(auth_url: str, email: str, password: str, log=print) -> bool:
    """Drive the z.ai sign-in in Camoufox (email form -> Aliyun slider -> consent)."""
    from camoufox.async_api import AsyncCamoufox

    log("  opening Camoufox to finish z.ai sign-in…")
    async with AsyncCamoufox(headless=False, humanize=True, os="windows", locale="en-US") as br:
        ctx = await br.new_context()
        page = await ctx.new_page()
        await page.goto(auth_url, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(4000)

        ok = await signin_zai_email(page, email, password, puzzles=25, log=log)
        if not ok:
            log("  z.ai sign-in failed")
            return False

        if "/auth/oauth/authorize" in page.url and "code=" not in page.url:
            log("  on consent screen, clicking Authorize…")
            if not await accept_consent(page):
                log("  consent click failed")
                return False

        # Wait for redirect back to localhost
        for _ in range(30):
            if "localhost" in page.url and "code=" in page.url:
                log(f"  redirected back to loopback: {page.url[:80]}…")
                await page.wait_for_timeout(2000)
                return True
            await page.wait_for_timeout(1000)

        log("  warning: redirect to localhost timed out")
        return False


def run_one(email: str, password: str, cdp_port: int = DEFAULT_PORT, log=print) -> bool:
    """Run one gentle AutoClaw claim cycle."""
    if not APPRUN.exists():
        log(f"AppRun not found: {APPRUN}")
        return False

    disp, xa = find_display_and_xauth()
    log(f"[1/7] Environment: DISPLAY={disp} XAUTHORITY={xa}")

    # Set up temporary xdg-open wrapper to catch the OAuth URL
    tmp_bin = Path(tempfile.mkdtemp(prefix="autoclaw_xdg_"))
    captured_file = tmp_bin / "captured_url.txt"
    wrapper = tmp_bin / "xdg-open"
    wrapper.write_text(f"""#!/usr/bin/env bash
for arg in "$@"; do
    if [[ "$arg" =~ "chat.z.ai" ]] || [[ "$arg" =~ "oauth" ]]; then
        echo "$arg" > "{captured_file}"
        exit 0
    fi
done
exec /usr/bin/xdg-open "$@"
""")
    wrapper.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{tmp_bin}:{env.get('PATH', '')}"
    env["DISPLAY"] = disp
    if xa:
        env["XAUTHORITY"] = xa

    log("[2/7] Launching patched AutoClaw AppRun with CDP :%d…" % cdp_port)
    cmd = [
        str(APPRUN),
        "--ozone-platform=x11",
        "--no-sandbox",
        f"--remote-debugging-port={cdp_port}",
    ]
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        # Give Electron 6 seconds to spin up UI & open CDP port
        time.sleep(6)

        log("[3/7] CDP: clicking 'Continue with Zai'…")
        clicked = asyncio.run(drive_cdp_continue_zai(cdp_port, log=log))
        if not clicked:
            log("  warning: 'Continue with Zai' not clicked via CDP, waiting for UI…")

        # Wait for the Aliyun security popup to render, then click with xdotool
        log("[4/7] Waiting 3s for Aliyun Security Verification window…")
        time.sleep(3)
        for attempt in range(1, 4):
            log(f"  xdotool click attempt {attempt}/3…")
            if click_security_verification_window(log=log):
                break
            time.sleep(2)

        # Wait for xdg-open wrapper to catch the auth URL
        log("[5/7] Waiting for shell.openExternal -> xdg-open wrapper…")
        auth_url = ""
        for _ in range(20):
            if captured_file.exists():
                auth_url = captured_file.read_text().strip()
                if auth_url:
                    break
            time.sleep(1)

        if not auth_url:
            log("  FAILED: xdg-open wrapper captured no OAuth URL")
            return False

        log(f"  captured auth URL: {auth_url[:70]}…")

        # Drive z.ai login via Camoufox
        log(f"[6/7] Completing z.ai OAuth login for {email}…")
        ok = asyncio.run(drive_zai_oauth_login(auth_url, email, password, log=log))
        if not ok:
            log("  z.ai login half failed")
            return False

        # Wait for the app's internal loopback handler (:18432) to process code & write vault
        log("[7/7] Waiting for AutoClaw to exchange tokens and write vault…")
        time.sleep(5)

        # Trigger claim_daily_credits using fresh token
        creds = load_app_credentials()
        tok = (creds or {}).get("access_token")
        if not tok:
            log("  token not detected from get_token or vault yet")
            return True

        log(f"  token acquired: {tok[:15]}… Triggering 40M daily/event claim…")
        client = AutoClawClient()
        claim_res = client.claim_daily_credits(tok)
        log(f"  claim response: {claim_res}")

        wallets = client.get_wallets(tok)
        instances = client.get_wallet_instances(tok)
        log(f"  wallets: {wallets.get('data')}")
        log(f"  wallet instances (scopes): {instances.get('data')}")
        return True
    finally:
        shutil.rmtree(tmp_bin, ignore_errors=True)
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def main():
    parser = argparse.ArgumentParser(description="AutoClaw 1-account gentle claim runner.")
    parser.add_argument("--email", help="z.ai account email (defaults to accounts.txt)")
    parser.add_argument("--password", help="z.ai password")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--dry-run", action="store_true", help="Print target account and exit")
    args = parser.parse_args()

    email = args.email
    password = args.password
    if not email or not password:
        pair = pick_account_from_farm()
        if not pair:
            sys.exit("Error: no email/password provided and no accounts.txt found.")
        email, password = pair

    print(f"Target account: {email}")
    if args.dry_run:
        print("[dry-run] account ready, AppRun exists:", APPRUN.exists())
        return

    ok = run_one(email, password, cdp_port=args.cdp_port)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
