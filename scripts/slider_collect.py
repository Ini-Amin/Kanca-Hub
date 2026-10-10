#!/usr/bin/env python3
"""
scripts/slider_collect.py — collect Aliyun slider (background, piece) image pairs.

No solving, no captcha pass/fail: it just opens each fresh puzzle, saves the two
images, and refreshes. The gap is later labeled by a WORKER's agent vision
(accurate, <2px) and stored in the dataset for reuse. Collect once, label once,
reuse forever — the shipped CLI is pure numpy/PIL, no heavy deps.

Usage:
  camoufox-venv/bin/python scripts/slider_collect.py --count 40 --out tests/fixtures/slider/raw
"""
from __future__ import annotations

import argparse
import asyncio
import base64
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
    box = M.open_mailbox("auto", domain="kancalabs.biz.id", log=log)
    got = 0

    async with AsyncCamoufox(headless=headless, humanize=True, os="windows") as browser:
        page = await browser.new_page()

        async def fresh():
            await page.goto(AUTH, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
            await page.get_by_text("Continue with Email").first.click()
            await page.wait_for_timeout(2000)
            await page.evaluate("""()=>{const e=[...document.querySelectorAll('button,a,div,span')]
                .filter(x=>(x.innerText||'').trim()==='Sign up'&&x.children.length===0);
                if(e.length)e[e.length-1].click();}""")
            await page.wait_for_timeout(2500)
            nm = await page.query_selector('input[placeholder*="Name" i]')
            if nm:
                await nm.fill("Collector")
            await page.fill('input[type="email"]', box.address)
            await page.fill('input[type="password"]', "Zx9!mqR7pLs2Vw")
            await CS._wait_open(page)

        await fresh()
        for i in range(1, count + 1):
            imgs = await CS._read_images(page)
            if not imgs:
                await fresh()
                continue
            stamp = str(int(time.time() * 1000))
            (out / f"{stamp}_bg.png").write_bytes(base64.b64decode(imgs[0].split(",", 1)[-1]))
            (out / f"{stamp}_pc.png").write_bytes(base64.b64decode(imgs[1].split(",", 1)[-1]))
            got += 1
            log(f"  [{i}/{count}] saved {stamp}")
            # fresh puzzle for the next one (refresh keeps the form)
            await CS.refresh(page)
            await page.wait_for_timeout(900)
            if not await CS._read_images(page):
                await fresh()
        log(f"done: {got} pairs -> {out}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Collect slider image pairs (no solving)")
    ap.add_argument("--count", type=int, default=40)
    ap.add_argument("--out", default="tests/fixtures/slider/raw")
    ap.add_argument("--headless", action="store_true")
    a = ap.parse_args(argv)
    asyncio.run(run(count=a.count, out=Path(a.out), headless=a.headless))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())