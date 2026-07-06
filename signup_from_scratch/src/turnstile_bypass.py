"""
Turnstile Bypass — Solves Cloudflare Turnstile using nodriver's built-in verify_cf().

The ONLY approach that works (July 2026). No OpenCV, no xdotool, no template matching.

Flow:
1. Call verify_cf() → nodriver clicks the Turnstile checkbox, solver does its thing
2. Turnstile widget callback fires → populates cf-turnstile-response hidden input
3. Extract the token from DOM (with fallback to iframe extraction)
4. Inject token into ALL hidden inputs for safety
"""

import asyncio
from typing import Optional


async def verify_cf(page, timeout: float = 30.0) -> str:
    """Solve Turnstile using nodriver's built-in method. Returns cf_clearance or token."""
    result = await page.verify_cf()
    return result or ""


async def extract_turnstile_token(page, timeout: int = 15) -> Optional[str]:
    """
    Extract the actual cf-turnstile-response token from the DOM.
    
    Tries multiple methods:
    1. Read from hidden input (fastest — callback already fired)
    2. Read from Turnstile iframe's response data (bypasses callback issues)
    3. Fall back to cf_clearance cookie (last resort — might not work for signup)
    """
    # Method 1: Read from hidden input
    for _ in range(timeout):
        token = await page.evaluate("""
            (() => {
                const el = document.querySelector('input[name="cf-turnstile-response"]');
                return el && el.value && el.value.length > 10 ? el.value : null;
            })()
        """)
        if token:
            return token
        await asyncio.sleep(1)

    # Method 2: Try to extract from Turnstile iframe/window
    token = await page.evaluate("""
        (() => {
            // Try to get the Turnstile widget instance and extract response
            const iframes = document.querySelectorAll('iframe');
            for (const iframe of iframes) {
                if (iframe.src && iframe.src.includes('challenges.cloudflare.com')) {
                    try {
                        // Access the Turnstile callback data
                        const win = iframe.contentWindow;
                        if (win && win._turnstileResponse) return win._turnstileResponse;
                    } catch(e) {}
                }
            }
            
            // Check for data attributes on the widget container
            const widget = document.querySelector('.cf-turnstile');
            if (widget && widget.dataset.response) return widget.dataset.response;
            
            // Check all inputs again (some CF pages use different names)
            const altInputs = document.querySelectorAll(
                'input[name="cf_challenge_response"], ' +
                'input[name="g-recaptcha-response"], ' +
                'input[data-callback]'
            );
            for (const inp of altInputs) {
                if (inp.value && inp.value.length > 10) return inp.value;
            }
            
            return null;
        })()
    """)
    if token:
        return token

    # Method 3: Extract cf_clearance cookie (not the same, but fallback)
    try:
        cookies = await page.send(page.browser.cdb.network.get_cookies(["https://cloudflare.com"]))
        for c in cookies.get("cookies", []):
            if c.get("name") == "cf_clearance":
                return c.get("value")
    except Exception:
        pass

    return None


async def inject_turnstile_token(page, token: str) -> bool:
    """Inject token into all possible Turnstile/Recaptcha hidden inputs."""
    result = await page.evaluate(f"""
        (() => {{
            let count = 0;
            const selectors = [
                'input[name="cf-turnstile-response"]',
                'input[name="cf_challenge_response"]',
                'textarea[name="cf-turnstile-response"]',
                'textarea[name="g-recaptcha-response"]',
                'input[name="g-recaptcha-response"]',
            ];
            for (const sel of selectors) {{
                const el = document.querySelector(sel);
                if (el) {{
                    el.value = '{token}';
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    count++;
                }}
            }}
            return count;
        }})()
    """)
    return bool(result) and int(result) > 0


async def is_turnstile_present(page) -> bool:
    """Check if Turnstile CAPTCHA is present."""
    return bool(await page.evaluate('''(() => {
        const body = document.body ? document.body.innerText : '';
        if (body.includes("Verify you are human") || body.includes("Let us know you are human")) return true;
        const iframes = document.querySelectorAll("iframe");
        for (const f of iframes) {
            if (f.src && f.src.includes("challenges.cloudflare.com")) return true;
        }
        if (document.querySelector('input[name="cf-turnstile-response"]')) return true;
        if (document.querySelector('.cf-turnstile')) return true;
        return false;
    })()'''))
