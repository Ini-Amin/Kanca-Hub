#!/usr/bin/env python3
"""Tencent Lighthouse free-tier auto-claim, per saved account.

  tencent_claim.py add NAME   open a real window; log in + phone-verify once, then close it
  tencent_claim.py gui        zenity checklist: which accounts auto-claim
  tencent_claim.py claim      (timer) if stock is open, try each picked account once

Each account is a Camoufox profile in ~/.config/auto-freecf/tencent/<NAME>.
It clicks through free-trial buttons only; it never types payment details.
"""
import asyncio, json, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from tencent_lighthouse_watch import CLAIM_URL, is_open, poll

ROOT = Path.home() / ".config" / "auto-freecf" / "tencent"
PICKED, DONE = ROOT / "picked.json", ROOT / "claimed.json"
STEPS = r"text=/^(Free Trial for 3 months|Try (it )?now|Get (it )?now|Claim|Confirm|Submit|Agree)/i"


def load(p, d): return json.loads(p.read_text()) if p.exists() else d


def accounts(): return sorted(d.name for d in ROOT.iterdir() if d.is_dir()) if ROOT.exists() else []


async def browse(name, headless, fn):
    from camoufox.async_api import AsyncCamoufox
    async with AsyncCamoufox(persistent_context=True, user_data_dir=str(ROOT / name),
                             headless=headless, geoip=True, os="windows") as ctx:
        p = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await p.goto(CLAIM_URL, timeout=90000)
        return await fn(p)


async def wait_closed(p):
    print("Log in (and verify your phone) in the window, then close it.")
    await p.wait_for_event("close", timeout=0)


async def click_through(p):
    await p.wait_for_timeout(6000)
    card = p.locator("text=Lighthouse").first.locator("xpath=ancestor::*[.//span][3]")
    await card.locator("text=/Free Trial for 3 months/i").first.click()
    for _ in range(6):  # follow the free-trial wizard; stop if a payment form shows up
        await p.wait_for_timeout(4000)
        if await p.locator("text=/card number|payment method/i").count(): return "stopped: payment form"
        btn = p.locator(STEPS).last
        if not await btn.count(): break
        await btn.click()
    shot = ROOT / f"last_{int(time.time())}.png"; await p.screenshot(path=str(shot))
    return f"clicked through, check {shot} / {p.url}"


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "gui"
    ROOT.mkdir(parents=True, exist_ok=True)
    if cmd == "add":
        asyncio.run(browse(sys.argv[2], False, wait_closed))
    elif cmd == "gui":
        names = accounts()
        if not names: return print("no accounts yet: tencent_claim.py add NAME")
        picked, done = load(PICKED, []), load(DONE, {})
        rows = [x for n in names for x in (str(n in picked).upper(), n, done.get(n, "-"))]
        r = subprocess.run(["zenity", "--list", "--checklist", "--title=Tencent auto-claim",
                            "--text=Which accounts should auto-claim free Lighthouse at the 14:00 WIB reset?",
                            "--column=Use", "--column=Account", "--column=Claimed", "--separator=\n",
                            "--width=480", "--height=320", *rows], capture_output=True, text=True)
        if r.returncode == 0:
            PICKED.write_text(json.dumps(r.stdout.split())); print("picked:", r.stdout.split())
    elif cmd == "claim":
        if not is_open(poll(11135)): return print("Lighthouse sold out; nothing to do")
        done = load(DONE, {})
        for n in [n for n in load(PICKED, []) if n not in done]:
            res = asyncio.run(browse(n, "virtual", click_through)); print(n, res)
            if res.startswith("clicked"): done[n] = time.strftime("%Y-%m-%d %H:%M")
            DONE.write_text(json.dumps(done))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
