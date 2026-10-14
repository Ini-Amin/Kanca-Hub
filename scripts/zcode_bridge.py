#!/usr/bin/env python3
"""scripts/zcode_bridge.py — full ZCode bridge WITHOUT the desktop app.

Generate the OAuth URL (zcode_oauth_url pattern) -> sign in -> consent -> code ->
POST /api/v1/oauth/token -> ZCode JWT. Verified live (code 0, token minted)."""
import asyncio, os, sys, json, secrets, urllib.request, urllib.parse
sys.path.insert(0,'/home/amen/Auto-FreeCF/scripts')
import captcha_slider as CS
from consent_hard import accept_consent
from playwright.async_api import async_playwright
CDP="http://127.0.0.1:9222"
CLIENT_ID="client_P8X5CMWmlaRO9gyO-KSqtg"
REDIRECT_URI="https://zcode.z.ai/app/oauth/login?redirect=zcode://oauth/callback"
# multi-account: ZB_EMAIL/ZB_PW env or argv[1]/argv[2] (used by scripts/zcode.py mint)
def _zacct():
    """ZCode login from ZB_EMAIL/ZB_PW or ~/.config/auto-freecf/zcode_account.json (never committed)."""
    import json, os
    p = os.path.expanduser("~/.config/auto-freecf/zcode_account.json")
    d = json.load(open(p)) if os.path.exists(p) else {}
    return os.environ.get("ZB_EMAIL") or d.get("email", ""), os.environ.get("ZB_PW") or d.get("password", "")

EMAIL=(sys.argv[1] if len(sys.argv)>1 else '') or _zacct()[0]
PW=(sys.argv[2] if len(sys.argv)>2 else '') or _zacct()[1]
OUT=os.environ.get("ZB_OUT") or os.path.expanduser("~/.zcode-bridge/zc_jwt.txt")
JS="(t)=>{const e=[...document.querySelectorAll('button,a,div,span')].filter(x=>(x.innerText||'').trim()===t&&x.children.length===0);if(e.length){e[e.length-1].click();return true}return false}"
# The final OAuth hop lands on zcode.z.ai/app/oauth/login?code=... — a page whose
# only job is to bounce to the zcode:// desktop app ("Open ZCode"). We never need
# to render it: intercept the navigation, read `code` off the URL and answer with
# a stub so the "Open ZCode" prompt never appears and no deep-link tab lingers.
LANDING="https://zcode.z.ai/app/oauth/login"
def _code_in(url):
    q=urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    return (q.get("code") or [""])[0]
async def _grab_code(page, store):
    """Route handler: swallow the desktop-app landing, keep the auth code."""
    async def handler(route):
        c=_code_in(route.request.url)
        if c and not store.get("code"):
            store["code"]=c
        await route.fulfill(status=200, content_type="text/html",
                            body="<html><body>ok</body></html>")
    try:
        await page.route("**/app/oauth/login*", handler)
    except Exception:
        pass
def exchange(code,state):
    req=urllib.request.Request("https://zcode.z.ai/api/v1/oauth/token",
      data=json.dumps({"provider":"zai","code":code,"redirect_uri":REDIRECT_URI,"state":state}).encode(),
      method="POST",headers={"Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=30) as r: return json.loads(r.read().decode())
async def main():
    state=secrets.token_hex(16)
    url="https://chat.z.ai/auth?"+urllib.parse.urlencode(
        {"response_type":"code","client_id":CLIENT_ID,"redirect_uri":REDIRECT_URI,
         "app_version":"3.14.5","state":state}, quote_via=urllib.parse.quote)
    async with async_playwright() as p:
        b=await p.chromium.connect_over_cdp(CDP)
        ctx=b.contexts[0] if b.contexts else await b.new_context()
        page=await ctx.new_page()
        store={}
        await _grab_code(page, store)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000); await page.wait_for_timeout(4000)
            cap = False
            try:
                await page.get_by_text("Continue with Email").first.click(timeout=6000); await page.wait_for_timeout(2000)
                await page.fill('input[type="email"]', EMAIL); await page.fill('input[type="password"]', PW)
                cap = await CS.solve_aliyun(page, puzzles=25)
                print("captcha:", cap, flush=True)
                # Only submit once the captcha passed: with a failed captcha "Sign in"
                # does not log in, and the page drifts onto the GitHub login chooser
                # (the old github detour). A REAL mouse click is required anyway -- a
                # DOM .click() does not submit this React form.
                if cap:
                    try:
                        await page.get_by_text("Sign in", exact=True).last.click(timeout=6000)
                    except Exception:
                        await page.evaluate(JS, "Sign in")
                    await page.wait_for_timeout(6000)
            except Exception as e: print("signin skip:", str(e)[:40], flush=True)
            if not cap:
                print("captcha failed -> not submitting (no code)", flush=True)
            # consent_hard clicks any "Continue" leaf -- on the login chooser that is
            # "Continue with Github". Only run it on the real OAuth authorize page.
            ok = False
            if cap and "/auth/oauth/authorize" in page.url and "code=" not in page.url:
                ok = await accept_consent(page)
            print("consent:", ok, "url:", page.url[:100], flush=True)
            # wait a beat for the redirect to the landing page the route swallows
            for _ in range(8):
                if store.get("code"):
                    break
                await page.wait_for_timeout(1000)
            q=urllib.parse.parse_qs(urllib.parse.urlparse(page.url).query)
            code=store.get("code") or (q.get('code') or [''])[0]
            if not code and "code=" in page.url: code=page.url.split("code=")[1].split("&")[0]
            print("code:", code, flush=True)
            if code:
                try:
                    r=exchange(code,state); print("EXCHANGE:", json.dumps(r)[:400], flush=True)
                    tok=(r.get("data") or {}).get("token")
                    if tok: open(OUT,"w").write(tok); print("JWT SAVED", tok[:24], "->", OUT, flush=True)
                except Exception as e:
                    print("exchange ERR", str(e)[:150], flush=True)
                    try: print("body", e.read().decode()[:200])
                    except Exception: pass
        finally:
            # never leave the OAuth tab behind on the shared CDP Chrome
            try: await page.close()
            except Exception: pass
asyncio.run(main())
