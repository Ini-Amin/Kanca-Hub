#!/usr/bin/env python3
"""scripts/autoclaw_farm.py — AutoClaw claim farm, 1 account per run (gentle).

Features:
  - 100% Virtual Headless: runs inside Xvfb (:99) so zero windows pop up on desktop.
  - Safe xdg-open wrapper: never opens the user's primary browser.
  - Correct Sequence: solves Aliyun WAF security popup first, then clicks "Continue with Zai".
  - Completes z.ai OAuth via Camoufox, exchanges tokens, and triggers 40M daily/event claim.
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
DEFAULT_VIRTUAL_DISPLAY = ":99"


class VirtualDisplay:
    """Manages an invisible Xvfb server so desktop windows never pop up."""
    def __init__(self, display: str = DEFAULT_VIRTUAL_DISPLAY, width: int = 1280, height: int = 800):
        self.display = display
        self.width = width
        self.height = height
        self.proc: subprocess.Popen | None = None

    def start(self, log=print) -> bool:
        if not shutil.which("Xvfb"):
            log("  Xvfb not found; falling back to current DISPLAY")
            return False
        # Clean up any stale lock on this display
        lock_file = Path(f"/tmp/.X11-unix/X{self.display.lstrip(':')}")
        if lock_file.exists():
            try:
                lock_file.unlink()
            except Exception:
                pass
        cmd = ["Xvfb", self.display, "-screen", "0", f"{self.width}x{self.height}x24", "-nolisten", "tcp"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)
        if self.proc.poll() is None:
            log(f"  virtual headless display {self.display} active ({self.width}x{self.height})")
            return True
        log(f"  failed to start Xvfb on {self.display}")
        self.proc = None
        return False

    def stop(self):
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None


def click_security_verification_window(disp: str, log=print) -> bool:
    """Find the Security verification popup in `disp` and click the Aliyun checkbox with xdotool."""
    env = os.environ.copy()
    env["DISPLAY"] = disp
    try:
        wids = subprocess.check_output(
            ["xdotool", "search", "--onlyvisible", "--name", ""],
            env=env, text=True, timeout=5
        ).splitlines()
    except Exception as e:
        log(f"  xdotool search error: {e}")
        return False

    target_wid = None
    for wid in wids:
        try:
            name = subprocess.check_output(
                ["xdotool", "getwindowname", wid], env=env, text=True, timeout=3
            ).strip()
            if any(k in name.lower() for k in ("security verification", "安全验证", "verification")):
                target_wid = wid
                log(f"  found Aliyun security window: wid={wid} name={name!r}")
                break
        except Exception:
            continue

    if not target_wid:
        return False

    # Coordinates pinned in AGENTS.md: click at (570, 423) relative to the security window
    try:
        subprocess.run(["xdotool", "windowactivate", "--sync", target_wid],
                       env=env, check=True, timeout=5)
        time.sleep(0.5)
        subprocess.run(["xdotool", "mousemove", "--window", target_wid, "570", "423",
                        "click", "1"], env=env, check=True, timeout=5)
        log("  xdotool clicked Aliyun WAF checkbox at (570, 423)")
        return True
    except Exception as e:
        log(f"  xdotool click error: {e}")
        return False


def pick_account_from_farm() -> tuple[str, str] | None:
    """Return an (email, password) candidate from accounts.txt."""
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
                    log(f"  CDP inspecting page: {title} ({page.url})")
                    # Try English selector
                    loc = page.get_by_text("Continue with Zai", exact=False).first
                    if await loc.count() and await loc.is_visible():
                        await loc.click()
                        log("  clicked 'Continue with Zai' via CDP")
                        clicked = True
                        break
                    # Try Chinese selector
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


async def drive_zai_oauth_login(auth_url: str, email: str, password: str, disp: str, log=print) -> bool:
    """Drive the z.ai sign-in in Camoufox inside virtual display (email form -> Aliyun slider -> consent)."""
    from camoufox.async_api import AsyncCamoufox

    log("  opening Camoufox inside virtual display to finish z.ai sign-in…")
    # Set DISPLAY to virtual display so Camoufox never appears on user monitor
    env = os.environ.copy()
    env["DISPLAY"] = disp

    async with AsyncCamoufox(headless=True, humanize=True, os="windows", locale="en-US") as br:
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


def run_one(email: str, password: str, cdp_port: int = DEFAULT_PORT,
            use_xvfb: bool = True, log=print) -> bool:
    """Run one gentle AutoClaw claim cycle."""
    if not APPRUN.exists():
        log(f"AppRun not found: {APPRUN}")
        return False

    vdisp = VirtualDisplay(DEFAULT_VIRTUAL_DISPLAY)
    disp = os.environ.get("DISPLAY") or ":0"
    if use_xvfb and vdisp.start(log=log):
        disp = vdisp.display

    log(f"[1/7] Environment: DISPLAY={disp} (virtual headless={use_xvfb})")

    # Set up safe xdg-open wrapper that NEVER touches user's real browser
    tmp_bin = Path(tempfile.mkdtemp(prefix="autoclaw_xdg_"))
    captured_file = tmp_bin / "captured_url.txt"
    log_file = tmp_bin / "xdg_log.txt"
    wrapper = tmp_bin / "xdg-open"
    # Pure URL trap: logs and writes captured URL, never executes real browser
    wrapper.write_text(f"""#!/usr/bin/env bash
for arg in "$@"; do
    echo "$arg" >> "{log_file}"
    if [[ "$arg" =~ "http" ]] || [[ "$arg" =~ "oauth" ]] || [[ "$arg" =~ "chat.z.ai" ]]; then
        echo "$arg" > "{captured_file}"
    fi
done
exit 0
""")
    wrapper.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{tmp_bin}:{env.get('PATH', '')}"
    env["DISPLAY"] = disp

    log("[2/7] Launching patched AutoClaw AppRun in virtual display with CDP :%d…" % cdp_port)
    cmd = [
        str(APPRUN),
        "--ozone-platform=x11",
        "--no-sandbox",
        f"--remote-debugging-port={cdp_port}",
    ]
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    try:
        # Give Electron 6 seconds to spin up
        time.sleep(6)

        # Step 3: Check and solve Alibaba WAF Security Verification FIRST (as requested)
        log("[3/7] Checking for Alibaba WAF Security Verification popup FIRST…")
        waf_solved = False
        for attempt in range(1, 6):
            if click_security_verification_window(disp, log=log):
                waf_solved = True
                log("  Alibaba WAF solved on initial startup!")
                time.sleep(2)
                break
            time.sleep(1)

        if not waf_solved:
            log("  no initial WAF popup found; proceeding to sign in option")

        # Step 4: Click "Continue with Zai" via CDP
        log("[4/7] CDP: clicking 'Continue with Zai'…")
        clicked = asyncio.run(drive_cdp_continue_zai(cdp_port, log=log))
        if not clicked:
            log("  retrying CDP click in 3s…")
            time.sleep(3)
            asyncio.run(drive_cdp_continue_zai(cdp_port, log=log))

        # If WAF pops up after clicking sign in, solve it now
        log("  checking if Alibaba WAF appeared after clicking sign in…")
        time.sleep(2)
        for attempt in range(1, 5):
            if click_security_verification_window(disp, log=log):
                log("  Alibaba WAF solved after clicking sign in!")
                break
            time.sleep(1.5)

        # Step 5: Wait for safe xdg-open wrapper to catch OAuth URL
        log("[5/7] Waiting for OAuth URL to be caught by wrapper (no browser opened)…")
        auth_url = ""
        for _ in range(25):
            if captured_file.exists():
                auth_url = captured_file.read_text().strip()
                if auth_url:
                    break
            time.sleep(1)

        if not auth_url:
            log("  FAILED: xdg-open wrapper captured no OAuth URL")
            if log_file.exists():
                log(f"  xdg-open received: {log_file.read_text()[:300]}")
            return False

        log(f"  captured auth URL safely: {auth_url[:75]}…")

        # Step 6: Drive z.ai login via Camoufox inside virtual display
        log(f"[6/7] Completing z.ai OAuth login for {email}…")
        ok = asyncio.run(drive_zai_oauth_login(auth_url, email, password, disp=disp, log=log))
        if not ok:
            log("  z.ai login half failed")
            return False

        # Step 7: Wait for AutoClaw loopback handler (:18432) to exchange tokens
        log("[7/7] Waiting for AutoClaw to exchange tokens and write vault…")
        time.sleep(6)

        # Trigger claim_daily_credits using fresh token
        creds = load_app_credentials()
        tok = (creds or {}).get("access_token")
        if not tok:
            log("  token not detected from get_token or vault yet")
            return True

        log(f"  TOKEN ACQUIRED: {tok[:15]}… Triggering 40M daily/event claim…")
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
            proc.wait(timeout=4)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        vdisp.stop()


def main():
    parser = argparse.ArgumentParser(description="AutoClaw 1-account gentle claim runner.")
    parser.add_argument("--email", help="z.ai account email (defaults to accounts.txt)")
    parser.add_argument("--password", help="z.ai password")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-xvfb", action="store_true", help="Run with real display instead of Xvfb")
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

    ok = run_one(email, password, cdp_port=args.cdp_port, use_xvfb=not args.no_xvfb)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
