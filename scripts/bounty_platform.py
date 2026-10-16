#!/usr/bin/env python3
"""Platform sessions + page fetch for bounty work (Bugcrowd, HackerOne).

Persistent camoufox profiles per platform, so login happens ONCE
(interactive, user-driven) and automation reuses the session:
  ~/.config/auto-freecf/sessions/{bugcrowd,hackerone}/

  python3 scripts/bounty_platform.py login --platform bugcrowd
      # headed browser opens; user logs in manually; waits for success (10 min)
  python3 scripts/bounty_platform.py fetch --platform bugcrowd --url URL
      # headless, returns rendered text for model parsing (scope, hacktivity)
  python3 scripts/bounty_platform.py status

No credentials are ever stored by this script — only the browser profile
(cookies/storage) that the user's own login creates.
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

HOME = Path.home()
SESS = HOME / ".config" / "auto-freecf" / "sessions"
VENV_PY = HOME / ".local" / "share" / "auto-freecf" / "venv" / "bin" / "python"

PLATFORMS = {
    "bugcrowd": {"login": "https://bugcrowd.com/user/sign_in",
                 "check": "Dashboard",
                 "hacktivity": "https://bugcrowd.com/hacktivity?search={q}"},
    "hackerone": {"login": "https://hackerone.com/users/sign_in",
                  "check": "Hacktivity",
                  "hacktivity": "https://hackerone.com/hacktivity?query={q}"},
}

RUNNER = """
import asyncio, json, sys, os
from camoufox.async_api import AsyncCamoufox
async def main():
    cfg = json.load(open(sys.argv[1]))
    state_file = os.path.join(cfg["profile"], "state.json")
    async with AsyncCamoufox(headless=cfg["headless"], humanize=True) as browser:
        if os.path.exists(state_file):
            ctx = await browser.new_context(storage_state=state_file)
        else:
            ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto(cfg["url"], timeout=90000)
        if cfg.get("wait_login"):
            import time
            t0 = time.time()
            while time.time() - t0 < 600:
                if cfg["check"] in (await page.content()):
                    await ctx.storage_state(path=state_file)
                    os.chmod(state_file, 0o600)
                    print("LOGIN-OK", flush=True)
                    return
                await asyncio.sleep(5)
            print("LOGIN-TIMEOUT", flush=True)
            return
        await page.wait_for_timeout(8000)
        print((await page.inner_text("body"))[:6000], flush=True)
asyncio.run(main())
"""


def run_cfg(cfg):
    import subprocess
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
        path = f.name
    runner = Path("/tmp/opencode/bp_runner.py")
    runner.write_text(RUNNER)
    p = subprocess.run([str(VENV_PY), str(runner), path],
                       capture_output=True, text=True, timeout=int(cfg.get("timeout", 700)))
    return p.returncode, (p.stdout or "")[-8000:], (p.stderr or "")[-500:]


def cmd_login(a):
    if a.platform not in PLATFORMS:
        sys.exit("platform: bugcrowd|hackerone")
    prof = str(SESS / a.platform)
    Path(prof).mkdir(parents=True, exist_ok=True)
    print("browser terbuka — login manual sebagai dirimu (maks 10 mnt)...")
    rc, out, _ = run_cfg({"headless": False, "profile": prof,
                          "url": PLATFORMS[a.platform]["login"],
                          "wait_login": True,
                          "check": PLATFORMS[a.platform]["check"], "timeout": 660})
    print(out.strip().splitlines()[-1] if out.strip() else "rc=%d" % rc)


def cmd_fetch(a):
    prof = str(SESS / (a.platform or "bugcrowd"))
    rc, out, err = run_cfg({"headless": True, "profile": prof, "url": a.url, "timeout": 180})
    print(out if out.strip() else "EMPTY rc=%d err=%s" % (rc, err.strip()[:200]))


def cmd_status(_a):
    for p in ("bugcrowd", "hackerone"):
        d = SESS / p
        print("  %-10s %s" % (p, "profile ada (%d file)" % len(list(d.glob("*"))) if d.is_dir() and any(d.iterdir()) else "belum login"))


def main():
    ap = argparse.ArgumentParser(prog="bounty_platform.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    lg = sub.add_parser("login")
    lg.add_argument("--platform", required=True)
    ft = sub.add_parser("fetch")
    ft.add_argument("--platform", default="bugcrowd")
    ft.add_argument("--url", required=True)
    sub.add_parser("status")
    a = ap.parse_args()
    {"login": cmd_login, "fetch": cmd_fetch, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
