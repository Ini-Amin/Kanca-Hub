#!/usr/bin/env python3
"""scripts/autoclaw_farm.py — AutoClaw claim farm, 1 account per run (gentle).

Features:
  - 100% Pure CDP: controls Electron main window and Aliyun captcha via Chrome DevTools Protocol.
  - Zero xdotool dependency: uses CDP Input.dispatchMouseEvent for native trusted click on Aliyun checkbox.
  - Headed by default: opens visible window on display :0 so interaction is observable.
  - Headless option (--headless): runs inside Xvfb (:99) if requested.
  - Safe xdg-open wrapper: intercepts OAuth URL without ever opening the user's real browser.
  - Solves z.ai OAuth in Camoufox with humanized kinematics (zero in-drag DOM probing).
  - Waits for AutoClaw token exchange on :18432 and triggers 40M daily/event claim.
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


def get_x11_env(disp: str = ":0") -> dict[str, str]:
    """Build a robust environment with DISPLAY and valid XAUTHORITY."""
    env = os.environ.copy()
    env["DISPLAY"] = disp
    if not env.get("XAUTHORITY") or not Path(env["XAUTHORITY"]).exists():
        candidates = list(Path("/run/user/1000").glob("xauth_*"))
        if candidates:
            candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            env["XAUTHORITY"] = str(candidates[0])
        elif (Path.home() / ".Xauthority").exists():
            env["XAUTHORITY"] = str(Path.home() / ".Xauthority")
    return env


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
        disp_num = self.display.lstrip(":")
        for p in (Path(f"/tmp/.X11-unix/X{disp_num}"), Path(f"/tmp/.X{disp_num}-lock")):
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass
        cmd = ["Xvfb", self.display, "-screen", "0", f"{self.width}x{self.height}x24", "-ac", "-nolisten", "tcp"]
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
        disp_num = self.display.lstrip(":")
        for p in (Path(f"/tmp/.X11-unix/X{disp_num}"), Path(f"/tmp/.X{disp_num}-lock")):
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass


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


async def drive_autoclaw_cdp_flow(cdp_port: int, captured_file: Path, log=print) -> str:
    """Drive AutoClaw via CDP: click Continue with Zai -> solve Aliyun captcha -> get OAuth URL."""
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        b = None
        for _ in range(30):
            try:
                b = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
                break
            except Exception:
                await asyncio.sleep(0.5)

        if not b:
            log("  CDP port never became available")
            return ""

        try:
            # 1. Click "Continue with Zai" in main window (Page 0)
            p0 = b.contexts[0].pages[0]
            log(f"  CDP connected to AutoClaw main window: {await p0.title()!r}")
            await p0.wait_for_timeout(1000)

            clicked = await p0.evaluate("""() => {
                const span = Array.from(document.querySelectorAll('span, div, button')).find(el => {
                    const t = (el.innerText || '').trim();
                    return t === 'Continue with Zai' || t === 'Continue with Z.ai' || t.includes('Z.ai');
                });
                if (!span) return false;
                const target = span.closest('button, [role="button"], div') || span;
                target.click();
                return true;
            }""")
            log(f"  CDP clicked Continue with Zai button: {clicked}")

            # 2. Wait for Window 2 (Security verification popup)
            p1 = None
            log("  waiting for Aliyun Security Verification window…")
            for _ in range(25):
                await asyncio.sleep(0.5)
                if len(b.contexts[0].pages) > 1:
                    p1 = b.contexts[0].pages[1]
                    break

            if not p1:
                log("  Window 2 (Security verification) did not appear")
                return ""

            # 3. Locate Aliyun checkbox and dispatch trusted CDP mouse click
            cb = p1.locator("#aliyunCaptcha-checkbox-icon")
            await cb.wait_for(timeout=15000)
            box = await cb.bounding_box()
            if not box:
                log("  Checkbox bounding box not found")
                return ""

            cx = box["x"] + box["width"] / 2
            cy = box["y"] + box["height"] / 2
            log(f"  Aliyun checkbox located at ({cx:.1f}, {cy:.1f}); dispatching CDP trusted mouse click…")
            cdp = await p1.context.new_cdp_session(p1)
            await cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": cx, "y": cy})
            await asyncio.sleep(0.15)
            await cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "button": "left", "clickCount": 1, "x": cx, "y": cy})
            await asyncio.sleep(0.15)
            await cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "button": "left", "clickCount": 1, "x": cx, "y": cy})
            log("  CDP trusted click dispatched on Aliyun checkbox!")

            # 4. Wait for safe wrapper to capture OAuth URL
            auth_url = ""
            for _ in range(30):
                await asyncio.sleep(0.5)
                if captured_file.exists() and captured_file.stat().st_size > 0:
                    auth_url = captured_file.read_text().strip()
                    if auth_url:
                        break

            return auth_url
        finally:
            await b.close()


async def drive_zai_oauth_login(auth_url: str, email: str, password: str,
                                disp: str, headless: bool = False, log=print) -> bool:
    """Drive the z.ai sign-in in Camoufox (email form -> Aliyun slider -> consent)."""
    from camoufox.async_api import AsyncCamoufox

    env = get_x11_env(disp)
    for k, v in env.items():
        os.environ[k] = v

    log(f"  opening Camoufox (headless={headless}) on {disp} to finish z.ai sign-in…")
    async with AsyncCamoufox(headless=headless, humanize=True, os="windows", locale="en-US") as br:
        ctx = await br.new_context()
        page = await ctx.new_page()

        # 1. Establish session on chat.z.ai first so OAuth code is cleanly bound
        log("  logging in to chat.z.ai first to establish active session…")
        await page.goto("https://chat.z.ai/auth", wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)
        await signin_zai_email(page, email, password, puzzles=20, log=log)
        await page.wait_for_timeout(4000)
        log(f"  session established on {page.url}!")

        # 2. Navigate to AutoGLM OAuth authorization URL with session active
        log("  navigating to AutoGLM OAuth authorize URL…")
        await page.goto(auth_url, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(3000)

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
            headless: bool = False, log=print) -> bool:
    """Run one gentle AutoClaw claim cycle."""
    if not APPRUN.exists():
        log(f"AppRun not found: {APPRUN}")
        return False

    vdisp = VirtualDisplay(DEFAULT_VIRTUAL_DISPLAY)
    if headless:
        if vdisp.start(log=log):
            disp = vdisp.display
        else:
            disp = os.environ.get("DISPLAY") or ":0"
    else:
        disp = os.environ.get("DISPLAY") or ":0"

    env = get_x11_env(disp)
    log(f"[1/7] Environment: DISPLAY={disp} XAUTHORITY={env.get('XAUTHORITY')} (headless={headless})")

    # Set up safe xdg-open wrapper that catches URL without touching primary browser
    tmp_bin = Path(tempfile.mkdtemp(prefix="autoclaw_xdg_"))
    captured_file = tmp_bin / "captured_url.txt"
    log_file = tmp_bin / "xdg_log.txt"
    wrapper = tmp_bin / "xdg-open"
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

    env["PATH"] = f"{tmp_bin}:{env.get('PATH', '')}"

    log(f"[2/7] Launching patched AutoClaw AppRun with CDP :{cdp_port}…")
    cmd = [
        str(APPRUN),
        "--ozone-platform=x11",
        "--no-sandbox",
        f"--remote-debugging-port={cdp_port}",
    ]
    electron_log_f = open("/tmp/autoclaw_electron.log", "w")
    proc = subprocess.Popen(cmd, env=env, stdout=electron_log_f, stderr=electron_log_f)

    try:
        # Step 3: Pure CDP flow to click Zai -> solve Aliyun checkbox -> capture OAuth URL
        log("[3/7] Driving pure CDP flow: Click Zai -> solve Aliyun checkbox via CDP…")
        auth_url = asyncio.run(drive_autoclaw_cdp_flow(cdp_port, captured_file, log=log))
        if not auth_url:
            log("  FAILED: Could not capture OAuth URL via CDP flow")
            if log_file.exists():
                log(f"  xdg-open received: {log_file.read_text()[:300]}")
            return False

        log(f"[4/7] Captured auth URL: {auth_url[:75]}…")

        # Step 5: Drive z.ai login via Camoufox
        log(f"[5/7] Completing z.ai OAuth login for {email}…")
        ok = asyncio.run(drive_zai_oauth_login(auth_url, email, password, disp=disp, headless=headless, log=log))
        if not ok:
            log("  z.ai login half failed")
            return False

        # Step 6: Wait for AutoClaw loopback handler (:18432) to exchange tokens
        log("[6/7] Waiting for AutoClaw to exchange tokens and write vault (polling up to 45s)…")
        tok = None
        for sec in range(1, 46):
            creds = load_app_credentials()
            tok = (creds or {}).get("access_token")
            if tok:
                log(f"  TOKEN ACQUIRED after {sec}s: {tok[:15]}…")
                break
            time.sleep(1)

        if not tok:
            log("  token not detected from get_token or vault after 45s")
            log_p = Path("/tmp/autoclaw_electron.log")
            if log_p.exists():
                content = log_p.read_text(errors="ignore")
                debug_lines = [l for l in content.splitlines() if "[EXCHANGE_DEBUG]" in l or "zwork.account" in l or "oauth" in l.lower()]
                if debug_lines:
                    log("  Exchange & Auth Diagnostics:")
                    for l in debug_lines[-10:]:
                        log(f"    {l}")
                else:
                    log("  Recent Electron logs:")
                    for l in content.splitlines()[-15:]:
                        log(f"    {l}")
            return False

        log("  [7/7] Triggering 40M daily/event claim…")
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
        if headless:
            vdisp.stop()


def main():
    parser = argparse.ArgumentParser(description="AutoClaw 1-account gentle claim runner.")
    parser.add_argument("--email", help="z.ai account email (defaults to accounts.txt)")
    parser.add_argument("--password", help="z.ai password")
    parser.add_argument("--cdp-port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--headless", action="store_true", help="Run in headless virtual display (Xvfb :99)")
    parser.add_argument("--no-xvfb", action="store_true", help="Run headed on real display :0 (default)")
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

    # Default is headed (headless=False)
    headless = args.headless and not args.no_xvfb
    ok = run_one(email, password, cdp_port=args.cdp_port, headless=headless)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
