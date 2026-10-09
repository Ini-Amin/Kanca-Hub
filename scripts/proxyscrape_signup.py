#!/usr/bin/env python3
"""
scripts/proxyscrape_signup.py — free ProxyScrape trial -> harvest the endpoint.

ProxyScrape's signup is email+password+confirm + a terms checkbox, gated by a
Cloudflare Turnstile that Camoufox solves for free (no Capsolver). This drives it
with a relay mailbox (for the verification mail), confirms, logs in, and pulls the
proxy endpoint/credentials from the dashboard into a local pool file.

Usage (camoufox venv; drives a browser):
  camoufox-venv/bin/python scripts/proxyscrape_signup.py --headless
  ... --inspect-only        # dump fields, create nothing
  ... --domain kancalabs.biz.id --out ~/petani-proxy/output/proxyscrape_residential.txt
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import random
import string
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SIGNUP = "https://dashboard.proxyscrape.com/v2/sign-up"
LOGIN = "https://dashboard.proxyscrape.com/v2/sign-in"
DASHBOARD = "https://dashboard.proxyscrape.com/v2/"
PROXY_QUAD_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3}){3}):(\d{2,5})")


def gen_password(n: int = 14) -> str:
    a = string.ascii_letters + string.digits + "!@#%*+-_"
    return "".join(random.choice(a) for _ in range(n))


class Relay:
    def __init__(self, domain: str):
        import github_farm as g
        self._g = g
        self.mailbox = g.create_relay_mailbox(domain=domain)
        self.password = gen_password()

    @property
    def email(self) -> str:
        return self.mailbox["email"]

    def poll(self) -> list[dict]:
        return self._g.poll_relay_inbox(self.mailbox["jwt"])


async def _check_terms(page) -> int:
    """Tick the terms checkbox(es)."""
    n = 0
    for cb in await page.query_selector_all('input[type="checkbox"]'):
        try:
            if not await cb.is_checked():
                await cb.check()
                n += 1
        except Exception:  # noqa: BLE001
            pass
    return n


async def _wait_enabled(page, locator, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            if await locator.is_enabled():
                return True
        except Exception:  # noqa: BLE001
            pass
        await page.wait_for_timeout(1000)
    return False


async def signup(*, domain: str, headless: bool, inspect_only: bool, timeout: float) -> dict:
    from camoufox.async_api import AsyncCamoufox

    relay = Relay(domain)
    out = {"email": relay.email, "password": relay.password, "proxies": [], "status": "start"}
    print(f"  • Email    : {relay.email}")
    print(f"  • Password : {relay.password}")

    async with AsyncCamoufox(headless=headless, os="windows", humanize=True) as browser:
        page = await browser.new_page()
        await page.goto(SIGNUP, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(4000)

        if inspect_only:
            print("  • URL:", page.url)
            for el in await page.query_selector_all("input,button"):
                tag = await el.evaluate("e=>e.tagName.toLowerCase()")
                print(f"    {tag} type={await el.get_attribute('type')} "
                      f"ph={await el.get_attribute('placeholder')}")
            return out

        await page.fill('input[type="email"]', relay.email)
        await page.fill('input[type="password"]', relay.password)
        pwds = await page.query_selector_all('input[type="password"]')
        if len(pwds) >= 2:
            await pwds[1].fill(relay.password)
        await _check_terms(page)

        submit = page.locator('button[type="submit"]').first
        # Submit enables once Turnstile resolves (Camoufox auto-solves).
        if not await _wait_enabled(page, submit, timeout=timeout):
            out["status"] = "turnstile_not_solved"
            return out
        print("  • turnstile solved, submitting")
        # Intercom's chat iframe overlays the button and steals the click; drop it.
        await page.evaluate("""() => {
            for (const id of ['intercom-container','intercom-frame','launcher']) {
                const e = document.getElementById(id); if (e) e.remove();
            }
            document.querySelectorAll('iframe[name^="intercom"]').forEach(e => e.remove());
        }""")
        await page.wait_for_timeout(500)
        await submit.click()
        await page.wait_for_timeout(4000)
        print("  • after submit:", page.url)
        body = (await page.text_content("body") or "").lower()
        out["after_submit_url"] = page.url

        # Verification: either an email link/code, or we are already in.
        if "sign-in" in page.url or "verify" in body or "confirm" in body or "check your" in body:
            print("  • waiting for verification mail…")
            link, code = _wait_verify(relay, timeout=timeout)
            if code:
                await _enter_code(page, code)
            elif link:
                await page.goto(link, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(3000)

        proxies = await _harvest(page)
        out["proxies"] = proxies
        out["status"] = "ok" if proxies else "no_proxies"
        return out


def _wait_verify(relay: Relay, timeout: float) -> tuple[str | None, str | None]:
    """Return (verification_link, code) from the relay inbox."""
    end = time.time() + timeout
    dbg = False
    while time.time() < end:
        for m in relay.poll():
            text = " ".join(str(m.get(k, "")) for k in ("subject", "body", "html", "text"))
            if not dbg and text.strip():
                print("  • MAILFULL:", text[:1200].replace("\n", " "))
                dbg = True
            link = _first_link(text)
            if link:
                return link, None
            m2 = re.search(r"\b(\d{4,8})\b", text)
            if m2 and ("proxyscrape" in text.lower() or "verif" in text.lower() or "code" in text.lower()):
                return None, m2.group(1)
        time.sleep(5)
    return None, None


def _first_link(text: str) -> str | None:
    text = text.replace("&amp;", "&")
    links = re.findall(r"https?://[^\s\"'<>)]+", text)
    def _img(u: str) -> bool:
        return bool(re.search(r"\.(png|jpe?g|gif|svg|webp|ico|css|js)(\?|$)", u, re.I))
    # 1) the real verification link: a verify/confirm/token URL.
    for u in links:
        if not _img(u) and re.search(r"(verify|confirm|activ|token|signup|sign-up)", u, re.I):
            return u
    # 2) any non-image vendor link that is not a static asset.
    for u in links:
        if not _img(u) and "proxyscrape" in u.lower() and "/img/" not in u.lower():
            return u
    return None


async def _enter_code(page, code: str) -> None:
    for sel in ('input[inputmode="numeric"]', 'input[name*="code" i]', 'input[type="text"]'):
        el = await page.query_selector(sel)
        if el:
            try:
                await el.fill(code)
                btn = page.get_by_role("button", name=re.compile("verify|continue|submit", re.I)).first
                if await btn.count():
                    await btn.click()
                await page.wait_for_timeout(3000)
                return
            except Exception:  # noqa: BLE001
                continue


async def _harvest(page) -> list[str]:
    try:
        await page.goto(DASHBOARD, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
    except Exception:  # noqa: BLE001
        pass
    text = await page.text_content("body") or ""
    quads = [f"{ip}:{port}" for ip, port in PROXY_QUAD_RE.findall(text)]
    creds = None
    um = re.search(r"(?:username|user)\s*[:]\s*([A-Za-z0-9._-]{4,})", text, re.I)
    pm = re.search(r"(?:password|pass)\s*[:]\s*([A-Za-z0-9._-]{4,})", text, re.I)
    if um:
        creds = um.group(1) + (f":{pm.group(1)}" if pm else "")
    if creds:
        return [f"{creds}@{q}" if "@" not in q else q for q in quads] or [creds]
    return quads


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="ProxyScrape free-trial signup + harvest")
    ap.add_argument("--domain", default="kancalabs.biz.id", help="relay mailbox domain")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--inspect-only", action="store_true")
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--out", default=str(Path.home() / "petani-proxy" / "output" / "proxyscrape_residential.txt"))
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        res = asyncio.run(signup(domain=a.domain, headless=a.headless,
                                 inspect_only=a.inspect_only, timeout=a.timeout))
    except Exception as e:  # noqa: BLE001
        print(f"✗ proxyscrape signup failed: {type(e).__name__}: {e}")
        return 1
    print(json.dumps(res, indent=2))
    pro = res.get("proxies") or []
    if pro:
        p = Path(a.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        have = set(p.read_text().splitlines()) if p.exists() else set()
        added = [x for x in pro if x not in have]
        if added:
            with open(p, "a", encoding="utf-8") as f:
                f.write("\n".join(added) + "\n")
        print(f"✓ {len(added)} new prox(y/ies) -> {p}")
    return 0 if res.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())