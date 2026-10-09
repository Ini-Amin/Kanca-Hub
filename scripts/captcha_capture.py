#!/usr/bin/env python3
"""
scripts/captcha_capture.py — record LABELED Aliyun slider examples.

For each puzzle it dumps the background + piece images BEFORE you solve it, then
waits while YOU solve it by hand, and saves the slider's final displacement (the
ground-truth answer). A handful of these lets us calibrate the detector + the
pixel->distance mapping far better than a screenshot.

Usage (camoufox venv, visible):
  DISPLAY=:0 camoufox-venv/bin/python scripts/captcha_capture.py --puzzles 6
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

OUT = Path("/tmp/opencode/cap_data")
AUTH = "https://chat.z.ai/auth"

_SLIDER = """() => {
  const s=document.getElementById('aliyunCaptcha-sliding-slider');
  const w=document.getElementById('aliyunCaptcha-window-float');
  const b=document.getElementById('aliyunCaptcha-captcha-body');
  return {
    left: s ? (s.style.left || getComputedStyle(s).left) : null,
    windowShown: w ? w.getBoundingClientRect().width>0 : false,
    barShown: b ? b.getBoundingClientRect().width>0 : false,
  };
}"""

_IMGS = """() => {
  const g=id=>document.getElementById(id);
  const bg=g('aliyunCaptcha-img'), pc=g('aliyunCaptcha-puzzle');
  if(!bg||!pc) return null;
  const r=bg.getBoundingClientRect();
  if(!r.width) return null;
  return {bg:bg.src, pc:pc.src, w:Math.round(r.width), h:Math.round(r.height)};
}"""


async def _puzzle(page) -> dict | None:
    """Open the widget and wait for the user to solve it; return a label."""
    for sel in ("#aliyunCaptcha-captcha-body", "#aliyunCaptcha-captcha-wrapper"):
        try:
            await page.click(sel, timeout=2500)
            break
        except Exception:  # noqa: BLE001
            continue
    await page.wait_for_timeout(2000)
    imgs = await page.evaluate(_IMGS)
    if not imgs:
        return None
    stamp = f"{int(time.time())}"
    bg_p = OUT / f"{stamp}_bg.png"
    pc_p = OUT / f"{stamp}_pc.png"
    bg_p.write_bytes(base64.b64decode(imgs["bg"].split(",", 1)[-1]))
    pc_p.write_bytes(base64.b64decode(imgs["pc"].split(",", 1)[-1]))
    print(f"\n[{stamp}] images: {bg_p.name} / {pc_p.name}  (bg {imgs['w']}x{imgs['h']})")
    print(f"[{stamp}] >>> SOLVE IT BY HAND now (drag the slider).")

    # Poll the slider handle every ~250ms; keep the last non-zero 'left' we see
    # (the window/slider vanish once solved, so we must catch it DURING the drag).
    t0 = time.time()
    last_left = None
    solved = False
    while time.time() - t0 < 360:
        st = await page.evaluate(_SLIDER)
        if st.get("left") not in (None, "0px", "0"):
            last_left = st["left"]
        if not st.get("windowShown") and last_left:
            solved = True
            break
        await page.wait_for_timeout(250)
    return {"stamp": stamp, "bg": bg_p.name, "pc": pc_p.name,
            "bg_w": imgs["w"], "bg_h": imgs["h"],
            "drag_left_px": last_left, "solved": solved}


async def main_async(puzzles: int) -> None:
    import mailboxes as M
    from camoufox.async_api import AsyncCamoufox

    OUT.mkdir(parents=True, exist_ok=True)
    box = M.open_mailbox("auto", domain="kancalabs.biz.id")
    print(f"inbox: {box.address}")
    labeled = []

    async with AsyncCamoufox(headless=False, humanize=True, os="windows") as browser:
        for n in range(1, puzzles + 1):
            page = await browser.new_page()
            await page.goto(AUTH, wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(4000)
            try:
                await page.get_by_text("Continue with Email").first.click()
                await page.wait_for_timeout(2000)
                await page.get_by_text("Sign up", exact=True).last.click()
                await page.wait_for_timeout(2500)
                await page.fill('input[type="email"]', box.address)
                await page.fill('input[type="password"]', "Zx9!mqR7pLs2Vw")
            except Exception as e:  # noqa: BLE001
                print(f"[{n}] setup error: {e}")
            label = await _puzzle(page)
            if label:
                label["puzzle"] = n
                labeled.append(label)
                print(f"[{n}] label: {json.dumps(label)}")
            (OUT / "labels.json").write_text(json.dumps(labeled, indent=2))
            await page.close()

        print(f"\nsaved {len(labeled)} labels -> {OUT/'labels.json'}")
        await browser.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Record labeled Aliyun slider examples")
    ap.add_argument("--puzzles", type=int, default=5)
    a = ap.parse_args(argv)
    asyncio.run(main_async(a.puzzles))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())