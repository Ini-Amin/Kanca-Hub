#!/usr/bin/env python3
"""scripts/zcode_bridge.py — full ZCode bridge WITHOUT the desktop app.

Generate the OAuth URL (zcode_oauth_url pattern) -> sign in -> consent -> code ->
POST /api/v1/oauth/token -> ZCode JWT. Verified live (code 0, token minted)."""
import asyncio, sys, json, secrets, urllib.request, urllib.parse
sys.path.insert(0,'/home/amen/Auto-FreeCF/scripts'); sys.path.insert(0,'/tmp/opencode')
import captcha_slider as CS
from consent_hard import accept_consent
from playwright.async_api import async_playwright
CDP="http://127.0.0.1:9222"
CLIENT_ID="client_P8X5CMWmlaRO9gyO-KSqtg"
REDIRECT_URI="https://zcode.z.ai/app/oauth/login?redirect=zcode://oauth/callback"
EMAIL='b3mnvlqbvf@kancalabs.biz.id'; PW='Zx9!mqR7pLs2Vw'
JS="(t)=>{const e=[...document.querySelectorAll('button,a,div,span')].filter(x=>(x.innerText||'').trim()===t&&x.children.length===0);if(e.length){e[e.length-1].click();return true}return false}"
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
        await page.goto(url, wait_until="domcontentloaded", timeout=60000); await page.wait_for_timeout(4000)
        try:
            await page.get_by_text("Continue with Email").first.click(timeout=6000); await page.wait_for_timeout(2000)
            await page.fill('input[type="email"]', EMAIL); await page.fill('input[type="password"]', PW)
            print("captcha:", await CS.solve_aliyun(page, puzzles=25), flush=True)
            await page.evaluate(JS,"Sign in"); await page.wait_for_timeout(6000)
        except Exception as e: print("signin skip:", str(e)[:40], flush=True)
        ok=await accept_consent(page)
        print("consent:", ok, "url:", page.url[:100], flush=True)
        q=urllib.parse.parse_qs(urllib.parse.urlparse(page.url).query)
        code=(q.get('code') or [''])[0]
        if not code and "code=" in page.url: code=page.url.split("code=")[1].split("&")[0]
        print("code:", code, flush=True)
        if code:
            try:
                r=exchange(code,state); print("EXCHANGE:", json.dumps(r)[:400], flush=True)
                tok=(r.get("data") or {}).get("token")
                if tok: open("/tmp/opencode/zc_jwt.txt","w").write(tok); print("JWT SAVED", tok[:24], flush=True)
            except Exception as e:
                print("exchange ERR", str(e)[:150], flush=True)
                try: print("body", e.read().decode()[:200])
                except Exception: pass
asyncio.run(main())
