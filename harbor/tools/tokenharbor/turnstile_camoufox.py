"""Free in-browser Turnstile solver for TokenHarbor (Camoufox, no Capsolver).

Opens the real page in a stealth Firefox, lets the Turnstile widget solve itself
(a genuine browser usually gets an auto-pass), and returns `cf-turnstile-response`.
Needs camoufox + playwright (camoufox-venv). Pass the SAME proxy the HTTP session
uses, or the token will be tied to a different IP.

CLI check:  python -m tools.tokenharbor.turnstile_camoufox [url] [proxy]
"""
from __future__ import annotations

import asyncio
import sys
from typing import Optional

# One shared token query: the hidden input Turnstile fills, else the widget API.
_READ_TOKEN = """() => {
  const i = document.querySelector('input[name="cf-turnstile-response"]');
  if (i && i.value) return i.value;
  try { return window.turnstile && window.turnstile.getResponse() || ''; } catch (e) { return ''; }
}"""


def _proxy_dict(proxy: Optional[str]) -> Optional[dict]:
    """http://user:pass@host:port -> playwright proxy dict."""
    if not proxy:
        return None
    from urllib.parse import unquote, urlparse
    u = urlparse(proxy)
    d = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
    if u.username:
        d["username"] = unquote(u.username)
    if u.password:
        d["password"] = unquote(u.password)
    return d


async def solve_async(page_url: str, proxy: Optional[str] = None,
                      timeout: float = 90, headless: bool = False) -> Optional[str]:
    from camoufox.async_api import AsyncCamoufox  # imported lazily: only in camoufox-venv

    kwargs: dict = dict(headless=headless, humanize=True, os="windows")
    pd = _proxy_dict(proxy)
    if pd:
        kwargs["proxy"] = pd
        kwargs["geoip"] = True  # match locale/timezone to the proxy exit
    async with AsyncCamoufox(**kwargs) as browser:
        page = await browser.new_page()
        await page.goto(page_url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        clicked = False
        start = loop.time()
        while loop.time() < deadline:
            token = await page.evaluate(_READ_TOKEN)
            if token:
                return token
            # No widget after ~10s: this egress isn't challenged (seen live on the
            # mobile tether). "" = "not required", distinct from None = "failed".
            if loop.time() - start > 10 and not any(
                    "challenges.cloudflare.com" in f.url for f in page.frames):
                return ""
            if not clicked and loop.time() > deadline - timeout + 12:
                # Widget sometimes needs a nudge: click the Turnstile iframe's checkbox area.
                for fr in page.frames:
                    if "challenges.cloudflare.com" in fr.url:
                        try:
                            box = await (await fr.frame_element()).bounding_box()
                            if box:
                                await page.mouse.click(box["x"] + 28, box["y"] + box["height"] / 2)
                        except Exception:  # noqa: BLE001 - best-effort nudge
                            pass
                clicked = True
            await asyncio.sleep(1.5)
    return None


def solve_turnstile(page_url: str, proxy: Optional[str] = None,
                    timeout: float = 90, headless: bool = False) -> Optional[str]:
    """Sync entry point used by client.py. Token, "" (no challenge shown) or None (failed)."""
    try:
        return asyncio.run(solve_async(page_url, proxy, timeout, headless))
    except Exception as e:  # noqa: BLE001
        print(f"  Camoufox Turnstile error: {type(e).__name__}: {e}")
        return None


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "https://tokenharbor.ai/login?mode=signup"
    prx = sys.argv[2] if len(sys.argv) > 2 else None
    tok = solve_turnstile(url, prx)
    print("TOKEN:", (tok[:40] + "...") if tok else None)
    raise SystemExit(0 if tok else 1)
