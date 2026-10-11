#!/usr/bin/env python3
"""scripts/consent_hard.py — accept the z.ai / ZCode OAuth consent screen.

Recreated (the /tmp copy was wiped). `accept_consent(page)` is awaited by
zcode_bridge.py after sign-in: the OAuth flow lands on an authorize/consent
page whose affirmative button must be clicked so the browser is redirected
back to the redirect_uri with ?code=<auth code>.

Strategy: wait for either (a) a redirect carrying `code=` already, or (b) a
consent button whose label matches an affirmative keyword. We probe common
label sets and, failing that, click the last <button>/<a>/<div>/<span> that
looks like a primary action. Returns True when a code appears or the page
left the consent screen.
"""
from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

# affirmative labels seen on chat.z.ai / zcode consent screens (en + zh)
AFFIRM = [
    "Authorize", "Authorize App", "同意", "授权", "确认", "Continue",
    "Accept", "Agree", "Allow", "确认授权", "同意并继续", "Continue anyway",
]

_CLICK_JS = """(t) => {
  const els = [...document.querySelectorAll('button,a,div,span')].filter(
    x => (x.innerText||'').trim() === t && x.children.length === 0);
  if (els.length) { els[els.length-1].click(); return true; }
  return false;
}"""


def _code_of(url: str) -> str:
    q = parse_qs(urlparse(url).query)
    return (q.get("code") or [""])[0]


async def accept_consent(page, timeout_ms: int = 20000) -> bool:
    """Click through the consent screen; True once we have an auth code."""
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
    clicked = False
    while asyncio.get_event_loop().time() < deadline:
        if _code_of(page.url):
            return True
        # The consent screen gates "Continue" behind a terms checkbox, and the
        # checkbox is React-controlled -> a synthetic .click() is ignored, so we
        # need a REAL mouse click at its coordinates.
        try:
            cb = page.locator("input[type=checkbox]").first
            if await cb.count() and await cb.is_visible():
                await cb.click(timeout=3000)
                await page.wait_for_timeout(600)
        except Exception:  # noqa: BLE001
            pass
        for label in AFFIRM:
            try:
                if await page.evaluate(_CLICK_JS, label):
                    clicked = True
                    await page.wait_for_timeout(1500)
                    break
            except Exception:  # noqa: BLE001
                continue
        else:
            if not clicked:
                # last resort: click the most button-like primary element
                try:
                    await page.evaluate(
                        """() => {
                          const cands=[...document.querySelectorAll('button')]
                            .filter(b=>b.offsetParent && !/cancel|skip|later|back/i.test(b.innerText||''));
                          if(cands.length) cands[cands.length-1].click();
                        }""")
                    clicked = True
                    await page.wait_for_timeout(1500)
                except Exception:  # noqa: BLE001
                    pass
        if _code_of(page.url):
            return True
        await page.wait_for_timeout(1000)
    return bool(_code_of(page.url))
