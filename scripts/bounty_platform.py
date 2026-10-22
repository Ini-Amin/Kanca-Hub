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
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

HOME = Path.home()
SESS = HOME / ".config" / "auto-freecf" / "sessions"

# Which interpreter runs the camoufox child.
#
# WHY THIS IS NOT A CONSTANT ANY MORE: the old value was a hardcoded laptop path
# (`~/.local/share/auto-freecf/venv/bin/python`). On any other box that path does
# not exist, so `subprocess.run` raised FileNotFoundError *before* the browser
# ever started — and because bounty_run.py's _render_text() discards stderr and
# returns "" on a non-zero exit, the pipeline reported "empty page - session
# expired? re-login bugcrowd" for every program. That is a perfect false
# diagnosis: it blames a login that was never used, and it looks like a session
# problem rather than a missing interpreter.
#
# Resolution order, first hit wins:
#   1. $BOUNTY_PY        explicit override (set it in a unit to pin an env)
#   2. this interpreter  if it can already import camoufox
#   3. the legacy venv   for boxes that already have the old layout
def _pick_python() -> Path:
    env = os.environ.get("BOUNTY_PY")
    if env:
        return Path(env)
    if importlib.util.find_spec("camoufox") is not None:
        return Path(sys.executable)
    legacy = HOME / ".local" / "share" / "auto-freecf" / "venv" / "bin" / "python"
    return legacy


VENV_PY = _pick_python()

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
        # Wait for the SCOPE TABLE, not a fixed sleep. Bugcrowd lazy-loads the
        # "Name / Location" table when the #scope anchor is requested, and on a
        # slow render 8 seconds was not enough: the same URL returned 24 targets
        # on one call and 0 on the next (measured on blu 2026-10-22), which made
        # the pipeline look like the program had no scope. Poll for the marker
        # and fall through after the cap so a genuinely table-less page still
        # returns its text instead of failing.
        try:
            await page.wait_for_selector("text=/name\\s*\\/\\s*location/i", timeout=25000)
        except Exception:
            pass
        await page.wait_for_timeout(1500)
        # Print the FULL body text, not a fixed prefix.
        #
        # This used to be [:6000]. The Targets table sits BELOW the program
        # description, and long descriptions (OpenAI Safety's is several
        # paragraphs) pushed the table past character 6000. parse_scope() then
        # found no "In Scope"/"Out of Scope" cards and auto reported
        # "page rendered but no in-scope targets parsed - layout change?" and
        # retired the program. The page had rendered perfectly; we had simply
        # truncated it. Print everything and let the caller decide what to parse.
        text = await page.inner_text("body")
        print(text, flush=True)
asyncio.run(main())
"""


def run_cfg(cfg):
    import subprocess
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(cfg, f)
        path = f.name
    runner = Path("/tmp/opencode/bp_runner.py")
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text(RUNNER)
    try:
        p = subprocess.run([str(VENV_PY), str(runner), path],
                           capture_output=True, text=True, timeout=int(cfg.get("timeout", 700)))
    except FileNotFoundError as e:
        # Say what actually broke. Swallowing this turned a missing interpreter
        # into "session expired" on every program, which sent us chasing logins.
        return 127, "", "interpreter not found: %s (set BOUNTY_PY)" % e.filename
    # Do NOT truncate stdout. This was `p.stdout[-8000:]`, which kept only the
    # TAIL of the rendered page — the nav and program header are at the top, the
    # Targets table follows the description, so a tail slice silently dropped the
    # very thing parse_scope() needs and the caller concluded "layout change".
    # Page text is data; pass all of it through.
    return p.returncode, (p.stdout or ""), (p.stderr or "")[-800:]


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
