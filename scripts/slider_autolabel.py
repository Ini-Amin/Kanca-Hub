#!/usr/bin/env python3
"""
scripts/slider_autolabel.py — grow the Aliyun slider dataset with NO human solves.

For each fresh puzzle we make ONE exact drag (measure-and-correct) at a guessed
gap, and the app tells us pass/fail. A PASS means the guess WAS the true gap, so
we append (background, piece, gap) to the offline dataset. Repeat for N puzzles.

No torch: this is pure browser + numpy/PIL. The heavy DL model is never shipped;
only the resulting dataset (and a tiny detector fit from it) is.

Usage (camoufox venv drives the browser):
  camoufox-venv/bin/python scripts/slider_autolabel.py --count 60 --out tests/fixtures/slider/auto
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import random
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

AUTH = "https://chat.z.ai/auth"


async def run(*, count: int, out: Path, headless: bool, log=print) -> None:
    import mailboxes as M
    import captcha_slider as CS
    from camoufox.async_api import AsyncCamoufox

    out.mkdir(parents=True, exist_ok=True)
    labels_path = out / "labels.json"
    labels = json.loads(labels_path.read_text()) if labels_path.exists() else []
    have = {l.get("stamp") for l in labels}

    box = M.open_mailbox("auto", domain="kancalabs.biz.id", log=log)
    got = 0

    async with AsyncCamoufox(headless=headless, humanize=True, os="windows") as browser:
        page = await browser.new_page()
        await page.goto(AUTH, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(4000)
        await page.get_by_text("Continue with Email").first.click()
        await page.wait_for_timeout(2000)
        await page.evaluate("""()=>{const e=[...document.querySelectorAll('button,a,div,span')]
            .filter(x=>(x.innerText||'').trim()==='Sign up'&&x.children.length===0);
            if(e.length)e[e.length-1].click();}""")
        await page.wait_for_timeout(2500)
        nm = await page.query_selector('input[placeholder*="Name" i]')
        if nm:
            await nm.fill("Auto Label")
        await page.fill('input[type="email"]', box.address)
        await page.fill('input[type="password"]', "Zx9!mqR7pLs2Vw")

        async def _reset():
            """Fresh form + widget (needed after a PASS, when the widget won't reopen)."""
            await page.goto(AUTH, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
            await page.get_by_text("Continue with Email").first.click()
            await page.wait_for_timeout(2000)
            await page.evaluate("""()=>{const e=[...document.querySelectorAll('button,a,div,span')]
                .filter(x=>(x.innerText||'').trim()==='Sign up'&&x.children.length===0);
                if(e.length)e[e.length-1].click();}""")
            await page.wait_for_timeout(2500)
            nm2 = await page.query_selector('input[placeholder*="Name" i]')
            if nm2:
                await nm2.fill("Auto Label")
            await page.fill('input[type="email"]', box.address)
            await page.fill('input[type="password"]', "Zx9!mqR7pLs2Vw")
            await CS._wait_open(page)

        for i in range(1, count + 1):
            if not await CS._wait_open(page):
                await _reset()
                continue
            imgs = await CS._read_images(page)
            if not imgs:
                await _reset()
                continue
            # guess: detector, else a random offset in the common range
            g = CS.gap_by_diff(imgs[0], imgs[1])
            if g is None or not (40 <= g <= 295):
                g = random.choice([188, 200, 212, 224, 236, 176, 164, 248])
            bx = await CS._slider_box(page)
            if not bx:
                await _reset()
                continue
            await CS._drag(page, bx["x"], bx["y"], CS.invert(g))
            await page.wait_for_timeout(1100)
            ok = await CS._passed(page)
            if ok:
                stamp = str(int(time.time() * 1000))
                if stamp not in have:
                    (out / f"{stamp}_bg.png").write_bytes(base64.b64decode(imgs[0].split(",", 1)[-1]))
                    (out / f"{stamp}_pc.png").write_bytes(base64.b64decode(imgs[1].split(",", 1)[-1]))
                    labels.append({"stamp": stamp, "bg": f"{stamp}_bg.png",
                                   "pc": f"{stamp}_pc.png", "gap_x": g, "source": "autolabel"})
                    have.add(stamp); got += 1
                    labels_path.write_text(json.dumps(labels, indent=2))
                    log(f"  [{i}/{count}] PASS gap={g}  (total labels={len(labels)})")
                # captcha passed -> must RESET to keep collecting
                await _reset()
            else:
                await CS.refresh(page)
                await page.wait_for_timeout(700)
        log(f"done: +{got} labels -> {labels_path}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Auto-label Aliyun slider puzzles (no human)")
    ap.add_argument("--count", type=int, default=60)
    ap.add_argument("--out", default="tests/fixtures/slider/auto")
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args(argv)
    asyncio.run(run(count=a.count, out=Path(a.out), headless=a.headless))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())