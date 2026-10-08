#!/usr/bin/env python3
"""
scripts/otp_litensi_sms.py — phone-OTP provider backed by Litensi's Activation
(found under scope). Verification: driven through the live /otp UI in Camoufox.

Litensi has NO public API for phone activations (only /api/mail), and /otp is a
Laravel + Filament + Livewire page with Choices.js dropdowns, so this drives the
real UI:
  login -> /otp -> Country -> Service -> Price (cheapest) -> Make Order
  -> read the number from the Active list -> poll for the SMS code.

Verified live: Indonesia + "Google,youtube,Gmail" + Rp453 -> number
6283870792201 "Waiting for SMS...". Reading the code scans the page text (the
Active list is a Livewire component, not a plain <table>).

Implements the same shape gmail_creator's PhoneProvider expects: get_number(),
wait_code().

Usage (camoufox venv):
  python scripts/otp_litensi_sms.py --country Indonesia --service "Google,youtube,Gmail"
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

PHONE_RE = re.compile(r"\b(62\d{8,13})\b")
CODE_RE = re.compile(r"\b(\d{4,8})\b")


async def _pick_choice(page, sel_id: str, want: str) -> bool:
    cont = page.locator(f"#{sel_id}").locator("xpath=ancestor::div[contains(@class,'choices')][1]")
    try:
        await cont.click(timeout=6000)
    except Exception:
        await cont.click(force=True, timeout=6000)
    await asyncio.sleep(1.2)
    loc = page.locator(".choices__list--dropdown .choices__item").filter(has_text=want).first
    if await loc.count():
        await loc.click()
        return True
    return False


async def _pick_cheapest_price(page) -> str | None:
    cont = page.locator("#price").locator("xpath=ancestor::div[contains(@class,'choices')][1]")
    try:
        await cont.click(timeout=6000)
    except Exception:
        await cont.click(force=True, timeout=6000)
    await asyncio.sleep(1.5)
    opts = page.locator(".choices__list--dropdown .choices__item--selectable")
    for i in range(await opts.count()):
        t = (await opts.nth(i).inner_text()).strip()
        if re.fullmatch(r"\d+", t):
            await opts.nth(i).click()
            return t
    return None


async def order_number(country: str, service: str, *, headless: bool = False,
                       dry_run: bool = False) -> dict:
    """Order a Litensi phone number and return {'number', 'page'} (page stays open)."""
    from camoufox.async_api import AsyncCamoufox
    import litensi_account as L
    import residential_proxy_signup as R

    acct = L.load_account()
    browser = await AsyncCamoufox(headless=headless, os="windows", humanize=True).__aenter__()
    page = await browser.new_page()
    await L._do_login(page, R, acct["username"], acct["password"])
    await page.goto("https://litensi.id/otp", wait_until="networkidle", timeout=60000)
    await asyncio.sleep(3)
    if not await _pick_choice(page, "backup_country", country):
        raise RuntimeError(f"litensi: country {country!r} not selectable")
    await asyncio.sleep(4)
    if not await _pick_choice(page, "backup_service", service):
        raise RuntimeError(f"litensi: service {service!r} not available")
    await asyncio.sleep(5)
    price = await _pick_cheapest_price(page)
    await asyncio.sleep(2)
    if dry_run:
        return {"status": "dry_run", "price": price, "browser": browser, "page": page}
    try:
        await page.get_by_role("button", name="Make Order").first.click(timeout=10000)
    except Exception:
        pass
    await asyncio.sleep(8)
    body = await page.evaluate("() => (document.body&&document.body.innerText||'')")
    m = PHONE_RE.search(body)
    if not m:
        return {"status": "no_number", "price": price, "body": body[-300:], "browser": browser, "page": page}
    return {"status": "ordered", "number": m.group(1), "price": price,
            "browser": browser, "page": page}


class LitensiSms:
    """PhoneProvider-compatible: get_number() then wait_code()."""

    def __init__(self, country: str = "Indonesia", service: str = "Google,youtube,Gmail",
                 headless: bool = False):
        self.country, self.service, self.headless = country, service, headless
        self._loop = asyncio.new_event_loop()
        self._ctx: dict = {}

    def get_number(self) -> str:
        res = self._loop.run_until_complete(order_number(self.country, self.service, headless=self.headless))
        self._ctx = res
        if res.get("status") != "ordered":
            raise RuntimeError(f"litensi sms: {res.get('status')} ({res.get('body','')[:120]})")
        return res["number"]

    def wait_code(self, timeout: int = 120) -> str:
        page = self._ctx.get("page")
        if page is None:
            raise RuntimeError("litensi sms: no active order (call get_number first)")
        seen = self._ctx.get("number", "")

        async def _poll():
            import litensi_account as L  # noqa
            end = time.time() + timeout
            while time.time() < end:
                try:
                    body = await page.evaluate("() => (document.body&&document.body.innerText||'')")
                    # the SMS text sits in the active row; grab codes near the service
                    for m in CODE_RE.finditer(body):
                        c = m.group(1)
                        if c != seen and len(c) in (4, 5, 6):
                            return c
                except Exception:
                    pass
                await asyncio.sleep(4)
            return None
        code = self._loop.run_until_complete(_poll())
        if not code:
            raise RuntimeError("litensi sms: no code within timeout")
        return code


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Litensi phone-OTP provider (browser-driven)")
    ap.add_argument("--country", default="Indonesia")
    ap.add_argument("--service", default="Google,youtube,Gmail")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="select fields but do not Make Order")
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    res = asyncio.run(order_number(a.country, a.service, headless=a.headless, dry_run=a.dry_run))
    print("  result:", {k: v for k, v in res.items() if k not in ("browser", "page")})
    return 0 if res.get("status") in ("ordered", "dry_run") else 1


if __name__ == "__main__":
    sys.exit(main())