#!/usr/bin/env python3
"""
scripts/zooproxy_trial.py — sign up for ZooProxy (email+password+image captcha)
and open the free trial / read issued proxy credentials.

ZooProxy's "verify" is a 4-character image captcha (GET/POST api.zooproxy.com/ip2
returns a base64 PNG), not an email code. We OCR it with tesseract (installed),
retry on a wrong read (each retry gets a fresh captcha), then submit.

Usage (camoufox venv):
  camoufox-venv/bin/python scripts/zooproxy_trial.py --headless
  camoufox-venv/bin/python scripts/zooproxy_trial.py --captcha-only   # OCR test
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import random
import re
import string
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SIGNUP = "https://zooproxy.com/sign-up/"
LOGIN = "https://zooproxy.com/sign-in/"
LEDGER = Path.home() / ".config" / "auto-freecf" / "zooproxy_account.json"


def _pw() -> str:
    return "Zoo" + "".join(random.choices(string.ascii_letters + string.digits, k=6)) + "$" + \
        "".join(random.choices(string.digits, k=3))


def ocr_captcha(png_bytes: bytes) -> str:
    """tesseract on the 4-char captcha. Try a couple of page-seg modes; return best."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "c.png"
        p.write_bytes(png_bytes)
        for psm in ("8", "7", "13", "6"):
            try:
                out = subprocess.run(
                    ["tesseract", str(p), "-", "--psm", psm,
                     "-c", "tessedit_char_whitelist=" + string.ascii_letters],
                    capture_output=True, text=True, timeout=20).stdout
                cand = re.sub(r"[^A-Za-z]", "", out)
                if len(cand) >= 3:
                    return cand
            except Exception:
                continue
    return ""


async def _captcha_png(page) -> bytes:
    src = await page.evaluate("""() => {
        const imgs = [...document.querySelectorAll('img')];
        const c = imgs.find(i => (i.src||'').startsWith('data:image'));
        return c ? c.src : '';
    }""")
    if not src:
        return b""
    b64 = src.split(",", 1)[1] if "," in src else src
    return base64.b64decode(b64)


async def signup(*, headless: bool, max_captcha_tries: int = 8) -> dict:
    import mailboxes as M
    import residential_proxy_signup as R
    from camoufox.async_api import AsyncCamoufox

    local = "zoo" + "".join(random.choices(string.ascii_lowercase, k=6))
    mb = M.open_mailbox("auto", domain="kancalabs.my.id", local_part=local, log=print)
    email, password = mb.address, _pw()
    print(f"  • Email    : {email}")
    print(f"  • Password : {password}")

    async with AsyncCamoufox(headless=headless, os="windows", humanize=True) as browser:
        page = await browser.new_page()
        await page.goto(SIGNUP, wait_until="networkidle", timeout=60000)
        await asyncio.sleep(3)
        await R._fill_by_placeholder(page, "email", email)
        await R._fill_by_placeholder(page, "password", password)
        await asyncio.sleep(0.8)
        # #signup-btn is the real submit; use a JS click (an overlay div eats
        # pointer events, so a normal .click() times out but JS works).
        await page.evaluate("() => { const b=document.querySelector('#signup-btn'); if (b) b.click(); }")
        await asyncio.sleep(6)
        cur = page.url or ""
        print(f"  after signup -> {cur}")
        ok = "/sign-up" not in cur
        if ok:
            LEDGER.parent.mkdir(parents=True, exist_ok=True)
            LEDGER.write_text(f'{{"email":"{email}","password":"{password}"}}')
            LEDGER.chmod(0o600)
            return {"status": "registered", "email": email, "password": password, "url": cur}
        print("  ✗ signup did not advance")
        return {"status": "no_advance", "email": email, "password": password}


async def read_console(*, headless: bool) -> dict:
    """Log in and dump the console/wallet to see what the free trial offers."""
    import json
    import residential_proxy_signup as R
    from camoufox.async_api import AsyncCamoufox
    acct = json.loads(LEDGER.read_text()) if LEDGER.exists() else {}
    if not acct:
        print("  ✗ no saved account (run signup first)"); return {"status": "no_account"}
    async with AsyncCamoufox(headless=headless, os="windows", humanize=True) as browser:
        page = await browser.new_page()
        await page.goto(LOGIN, wait_until="networkidle", timeout=60000)
        await asyncio.sleep(3)
        await R._fill_by_placeholder(page, "email", acct["email"])
        await R._fill_by_placeholder(page, "password", acct["password"])
        # login may also want a captcha
        png = await _captcha_png(page)
        if png:
            code = ocr_captcha(png)
            box = page.locator("#verificat_box, input[placeholder*='erification' i]").first
            if await box.count():
                await box.fill(code)
        await R._click_text(page, "Log in", "Login")
        await asyncio.sleep(5)
        print("  url:", page.url)
        body = await page.locator("body").inner_text()
        print("  body:", body[:600].replace("\n", " | "))
        return {"status": "console", "url": page.url, "body": body[:600]}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="ZooProxy free-trial signup (+captcha OCR)")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--captcha-only", action="store_true", help="just OCR one captcha and exit")
    return ap


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.captcha_only:
        from camoufox.async_api import AsyncCamoufox
        async def _t():
            async with AsyncCamoufox(headless=True, os="windows", humanize=True) as b:
                p = await b.new_page()
                await p.goto(SIGNUP, wait_until="networkidle", timeout=60000)
                await asyncio.sleep(3)
                print("OCR:", ocr_captcha(await _captcha_png(p)))
        asyncio.run(_t()); return 0
    res = asyncio.run(signup(headless=a.headless))
    if res.get("status") == "registered":
        asyncio.run(read_console(headless=a.headless))
    return 0 if res.get("status") == "registered" else 2


if __name__ == "__main__":
    sys.exit(main())