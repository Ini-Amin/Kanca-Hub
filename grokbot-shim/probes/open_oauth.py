import asyncio, json
from playwright.async_api import async_playwright
d=json.load(open('/tmp/oauth_open.json'))
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(executable_path='/home/amen/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome',
                                  headless=True, args=['--no-sandbox'])
        ctx=await b.new_context()
        await ctx.add_cookies(d['cookies'])
        pg=await ctx.new_page()
        await pg.goto(d['url'], wait_until='domcontentloaded', timeout=60000)
        await pg.wait_for_timeout(3000)
        print("URL:", pg.url[:120])
        txt=await pg.evaluate("()=>document.body.innerText.slice(0,300)")
        print("TEXT:", txt.replace("\n"," | ")[:300])
        # try consent button
        for lab in ("Continue","Allow","Authorize","Agree","Confirm"):
            try:
                btn=pg.get_by_role("button",name=lab).first
                if await btn.count():
                    await btn.click(timeout=4000); print("clicked",lab); await pg.wait_for_timeout(3000)
            except Exception: pass
        print("URL after:", pg.url[:140])
        await b.close()
asyncio.run(main())
