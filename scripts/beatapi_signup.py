#!/usr/bin/env python3
"""
scripts/beatapi_signup.py — create a beatapi.io account and grab its API key.

Confirmed from live https://beatapi.io/sign-up HTML (read-only fetch):
  - inputs: #name (text), #email (email), #password (password); button 'Sign Up'.

[UNVERIFIED] guesses (API paths observed dynamically, not in static HTML):
  - JSON API endpoints for register/verify/keys — we sniff responses matching
    api.beatapi.io / /api/ instead of hardcoding them.
  - Dashboard API-keys section location; we look for key-shaped strings
    (sk-... / long base62) in the DOM, localStorage and sniffed JSON.

Flow: camoufox -> fill form -> captcha_slider if challenged -> verify email
via keyless Tempik mailbox (BEATAPI_MAIL_DOMAIN=kancalabs.my.id) -> open
dashboard -> capture key. Record: {email, password, name, api_key, status}.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import string
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

SIGNUP = "https://beatapi.io/sign-up"
DASHBOARD = "https://beatapi.io/dashboard"
LEDGER = Path.home() / ".config" / "auto-freecf" / "beatapi_accounts.jsonl"
MAIL_DOMAIN = os.environ.get("BEATAPI_MAIL_DOMAIN", "kancalabs.my.id")

_JS_CLICK = """(text) => {
  const norm = s => (s || '').replace(/\\s+/g, ' ').trim();
  const wants = norm(text).toLowerCase();
  const els = [...document.querySelectorAll('button, a, [role=button], input[type=submit]')];
  for (const el of els) {
    if (norm(el.innerText || el.value || '').toLowerCase() === wants) { el.click(); return true; }
  }
  return false;
}"""

_KEY_RE = r'(?:sk-|bk-|beat-)?[A-Za-z0-9_-]{28,}'


def gen_password(n=14):
    a = string.ascii_letters + string.digits + "!%*+-_"
    return "".join(random.choice(a) for _ in range(n))


def gen_name():
    a = random.choice(["alex", "sam", "jordan", "kai", "noah", "mia", "ava", "leo"])
    b = random.choice(["tan", "lee", "kim", "ng", "ong", "wong", "lim", "chan"])
    return f"{a} {b}".title() + str(random.randint(10, 99))


async def _submit(page, labels, log, timeout_ms=8000) -> bool:
    for lbl in labels:
        try:
            loc = page.get_by_text(lbl, exact=True)
            if await loc.count():
                await loc.last.click(timeout=timeout_ms)
                log(f"  clicked {lbl!r} (real)")
                return True
        except Exception:  # noqa: BLE001
            pass
        try:
            if await page.evaluate(_JS_CLICK, lbl):
                log(f"  clicked {lbl!r} (dom)")
                return True
        except Exception:  # noqa: BLE001
            pass
    return False


async def _solve_captcha(page, log, puzzles=20) -> bool:
    import captcha_slider as CS
    import camoufox_helpers as H
    kind = await H.detect_challenge(page)
    if kind is None:
        return True  # no challenge seen
    log(f"  challenge: {kind}")
    if "aliyun" in (kind or "") or "slider" in (kind or ""):
        return await CS.solve_aliyun(page, log=log, puzzles=puzzles)
    return False


async def _find_key(page, log) -> str | None:
    """Hunt for the API key on the dashboard: DOM text, inputs, localStorage,
    and sniffed JSON bodies."""
    import re
    cands: list[str] = []
    try:
        body = await page.evaluate("()=>document.body.innerText")
        cands += re.findall(_KEY_RE, body or "")
        vals = await page.evaluate(
            "()=>[...document.querySelectorAll('input,code,pre,[class*=key]')]"
            ".map(e=>e.value||e.innerText||'').join('\\n')")
        cands += re.findall(_KEY_RE, vals or "")
        ls = await page.evaluate(
            "()=>Object.entries(localStorage).map(([k,v])=>k+'='+v).join('\\n')")
        for m in re.finditer(r'"?(?:api[_-]?key|key|token)"?[:=]\\s*"?([^"\\s]{20,})', ls or "", re.I):
            cands.append(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    # prefer sk-/prefixed, then longest
    cands = [c for c in cands if not c.startswith("eyJ")]  # skip JWTs
    if not cands:
        return None
    cands.sort(key=lambda c: (not c.startswith(("sk-", "bk-", "beat-")), -len(c)))
    return cands[0]


async def _once(*, headless=True, log=print, puzzles=20) -> dict:
    import mailboxes as M
    from camoufox.async_api import AsyncCamoufox

    box = M.open_mailbox("auto", domain=MAIL_DOMAIN, log=log)
    email, password, name = box.address, gen_password(), gen_name()
    rec = {"email": email, "password": password, "name": name,
           "provider": "beatapi", "api_key": None, "status": "start"}
    log(f"  email: {email}  password: {password}  name: {name}")

    async with AsyncCamoufox(headless=headless, humanize=True, os="windows") as browser:
        page = await browser.new_page()
        api_hits: list[tuple[str, int, str]] = []

        async def on_resp(r):
            url = r.url
            if ("beatapi.io" in url or "api.beatapi" in url) and r.request.method in ("POST", "PUT"):
                try:
                    api_hits.append((url, r.status, (await r.text())[:200]))
                except Exception:  # noqa: BLE001
                    pass
        page.on("response", lambda r: asyncio.create_task(on_resp(r)))

        # 1. signup form (selectors confirmed from live HTML)
        await page.goto(SIGNUP, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(4000)
        for sel, val in (("#name", name), ("#email", email), ("#password", password)):
            try:
                await page.fill(sel, val, timeout=15000)
            except Exception as e:  # noqa: BLE001
                rec["status"] = f"fill_failed:{sel}"
                rec["note"] = f"{type(e).__name__}: {e}"
                return rec
        if not await _solve_captcha(page, log, puzzles=puzzles):
            rec["status"] = "captcha_failed"
            return rec
        await page.wait_for_timeout(1500)
        await _submit(page, ("Sign Up", "Sign up", "Create Account"), log)
        for _ in range(15):
            await page.wait_for_timeout(700)
            if api_hits or (await page.url) != SIGNUP:
                break
        rec["posts"] = [{"url": u, "status": s} for u, s, _ in api_hits]
        if api_hits and api_hits[0][1] >= 400:
            rec["status"] = f"signup_rejected:{api_hits[0][1]}"
            rec["note"] = api_hits[0][2][:120]
            return rec

        # 2. verification email -> follow link if one is sent
        link = None
        try:
            link = box.link(r"https?://\S+", timeout=120)
        except Exception:  # noqa: BLE001
            link = None
        log(f"  verify link: {(link or '(none)')[:90]}")
        if link:
            vp = await browser.new_page()
            await vp.goto(link, wait_until="domcontentloaded", timeout=60000)
            await vp.wait_for_timeout(6000)
            await _submit(vp, ("Verify", "Confirm", "Continue"), log)
            await vp.wait_for_timeout(3000)
            await vp.close()

        # 3. dashboard -> API key
        await page.goto(DASHBOARD, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(6000)
        # [UNVERIFIED] nav label for the keys page
        for lbl in ("API Keys", "API keys", "Keys", "api-keys"):
            try:
                await page.get_by_text(lbl, exact=True).first.click(timeout=3000)
                await page.wait_for_timeout(2500)
                break
            except Exception:  # noqa: BLE001
                pass
        # [UNVERIFIED] a create/reveal button may be needed
        await _submit(page, ("Create Key", "Create API Key", "Generate", "Reveal"), log)
        await page.wait_for_timeout(2500)

        key = await _find_key(page, log)
        for _ in range(8):  # sniffed JSON may land late
            if key:
                break
            await page.wait_for_timeout(1500)
            key = await _find_key(page, log)
        # also scan sniffed bodies
        if not key:
            import re
            for _, _, body in api_hits:
                m = re.search(r'"(?:api[_-]?key|key)"\s*:\s*"([^"]{20,})"', body)
                if m:
                    key = m.group(1)
                    break

        cookies = await page.context.cookies()
        rec.update({
            "api_key": key,
            "url": page.url,
            "cookies": "; ".join(f"{c['name']}={c['value']}" for c in cookies
                                 if "beatapi" in c.get("domain", "")),
            "status": "ok" if key else "signup_ok_no_key",
        })
        if not key:
            rec["note"] = "account created but no API key found on dashboard"
        log(f"  key={'<found>' if key else None}  url={page.url}")
        return rec


async def signup(*, headless=True, attempts=3, log=print) -> dict:
    last = {}
    for i in range(1, attempts + 1):
        log(f"=== attempt {i}/{attempts} ===")
        try:
            rec = await _once(headless=headless, log=log)
        except Exception as e:  # noqa: BLE001
            log(f"  error: {type(e).__name__}: {e}")
            rec = {"status": "error", "error": str(e)}
        last = rec
        if rec.get("status") == "ok":
            return rec
    return last


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Create a beatapi.io account + get its API key")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--attempts", type=int, default=3)
    ap.add_argument("-o", "--out", default=None, help="write the API key to this file")
    ap.add_argument("--out-json", default=None,
                    help="write the full record (incl. email/password) to this JSON file")
    a = ap.parse_args(argv)
    rec = asyncio.run(signup(headless=a.headless, attempts=a.attempts))
    if a.out_json:
        Path(a.out_json).write_text(json.dumps(rec))
    print(json.dumps({k: v for k, v in rec.items() if k != "api_key"}, indent=2))
    if rec.get("status") == "ok":
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with open(LEDGER, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        if a.out and rec.get("api_key"):
            Path(a.out).write_text(rec["api_key"])
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
