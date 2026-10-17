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


async def signin_zai_email(page, email: str, password: str, *, puzzles: int = 25,
                           log=print) -> bool:
    """Sign in to z.ai with an email/password pair on an open authorize page.

    Shared by zcode_bridge.py (ZCode) and autoclaw_client.py (AutoClaw): both
    drive chat.z.ai's OAuth screen, and both hit the same two traps.

    1. The Aliyun slider is rendered INSIDE the email form and must be solved
       BEFORE submitting — `Sign in` is a no-op until the widget reports a pass.
       This is the step that was missing when the flow was copied from the app
       side: click-through without a solve leaves the button inert and the page
       silently sits on the login chooser.
    2. The submit is a React form, so a synthetic .click() does not post it; a
       real mouse click (or Enter) is required.

    Returns True once the page left the email form (consent screen or redirect).
    """
    import captcha_slider as CS

    try:
        await page.get_by_text("Continue with Email", exact=False).first.click(timeout=8000)
        await page.wait_for_timeout(2500)
    except Exception as e:  # noqa: BLE001 — already on the consent screen
        log(f"  no email form ({str(e)[:40]}) — assuming an existing z.ai session")
        return True

    await page.fill('input[type="email"]', email, timeout=8000)
    await page.fill('input[type="password"]', password, timeout=8000)
    await page.wait_for_timeout(1500)
    log("  solving z.ai Aliyun slider…")
    if not await CS.solve_aliyun(page, puzzles=puzzles, log=log):
        log("  slider not solved — not submitting")
        return False

    try:
        await page.get_by_text("Sign in", exact=True).last.click(timeout=8000)
    except Exception:  # noqa: BLE001 — React form ignores synthetic clicks
        await page.evaluate(_CLICK_JS, "Sign in")
    await page.wait_for_timeout(8000)

    body = await page.evaluate("() => document.body.innerText || ''")
    return "access your Z.ai account" in body or "code=" in page.url
