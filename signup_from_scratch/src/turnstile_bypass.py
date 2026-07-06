"""
Turnstile Bypass — Multi-strategy solver for Cloudflare Turnstile.

Strategies (tried in order):
1. verify_cf() — nodriver built-in (most reliable)
2. Quick interaction — mouse move, tab, click around widget (triggers partial token)
3. Fallback — just return, let submit-first logic handle it

The insight: CF signup flow is lenient. A partial/interacted Turnstile
often passes. Only fall back to full solve if redirect fails.
"""

import asyncio
from typing import Optional


async def verify_cf(page, timeout: float = 60.0) -> str:
    """Solve Turnstile using nodriver's built-in method."""
    result = await page.verify_cf()
    return result or ""


async def quick_interact(page) -> bool:
    """
    Minimal interaction to trigger Turnstile partial token.
    Moves mouse around the widget, tabs, clicks nearby.
    Returns True if any interaction succeeded.
    """
    try:
        # Scroll widget into view
        await page.evaluate("""
            const w = document.querySelector('iframe[src*="challenges.cloudflare.com"]');
            if (w) w.scrollIntoView({block: 'center'});
        """)
        await asyncio.sleep(1)

        # Click near the Turnstile iframe (not on it — triggers detection)
        viewport = await page.evaluate("""
            (()=>{return {w:window.innerWidth, h:window.innerHeight}})()
        """)
        w, h = viewport.get("w", 800), viewport.get("h", 600)

        # Move mouse across center of page (where Turnstile usually sits)
        for x in range(int(w * 0.3), int(w * 0.7), 40):
            await page.evaluate(f"document.elementFromPoint({x},{int(h*0.5)})?.dispatchEvent(new MouseEvent('mousemove',{{clientX:{x},clientY:{int(h*0.5)},bubbles:true}}))")
            await asyncio.sleep(0.05)

        # Click the checkbox area if present
        clicked = await page.evaluate("""
            (() => {
                const cb = document.querySelector('.cb-i, .challenge, input[type="checkbox"]');
                if (cb) { cb.click(); return true; }
                return false;
            })()
        """)
        await asyncio.sleep(2)

        return True
    except Exception:
        return False


async def is_turnstile_present(page) -> bool:
    """Check if Turnstile CAPTCHA is present on page."""
    return bool(await page.evaluate('''(() => {
        if (document.querySelector('input[name="cf_challenge_response"]')) return true;
        if (document.querySelector('input[name="cf-turnstile-response"]')) return true;
        const iframes = document.querySelectorAll("iframe");
        for (const f of iframes) {
            if (f.src && f.src.includes("challenges.cloudflare.com")) return true;
        }
        const body = document.body.innerText;
        if (body.includes("Verify you are human") || body.includes("Let us know you are human")) return true;
        return false;
    })()'''))


async def solve_turnstile(page, quick: bool = False) -> str:
    """
    Multi-strategy Turnstile solver.

    Args:
        page: nodriver Tab
        quick: If True, short timeout + skip retry (for submit-first flow)

    Returns:
        Token string or empty string if failed
    """
    timeout = 15.0 if quick else 60.0

    # Strategy 1: verify_cf()
    try:
        token = await verify_cf(page, timeout=timeout)
        if token:
            return token
    except Exception:
        pass

    # Strategy 2: Quick interaction
    if quick:
        await quick_interact(page)
        return ""  # Don't block — let submit-first handle it

    # Strategy 3: Full solve with retry
    try:
        token = await verify_cf(page, timeout=60.0)
        if token:
            return token
    except Exception:
        pass

    # Strategy 4: Interaction + retry
    await quick_interact(page)
    await asyncio.sleep(3)

    try:
        token = await verify_cf(page, timeout=60.0)
        if token:
            return token
    except Exception:
        pass

    return ""
