import asyncio, json
from playwright.async_api import async_playwright
d=json.load(open('/tmp/oauth_open.json'))
async def main():
    async with async_playwright() as p:
        b=await p.chromium.launch(executable_path='/home/amen/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome',
                                  headless=True, args=['--no-sandbox'])
        ctx=await b.new_context(); await ctx.add_cookies(d['cookies'])
        pg=await ctx.new_page()
        await pg.goto(d['url'], wait_until='domcontentloaded', timeout=60000)
        await pg.wait_for_timeout(2500)
        btn=pg.get_by_role("button",name="Continue").first
        print("continue count:", await btn.count())
        await btn.click(timeout=8000)
        await pg.wait_for_timeout(6000)
        print("URL:", pg.url[:160])
        print("TEXT:", (await pg.evaluate("()=>document.body.innerText.slice(0,200)")).replace("\n"," | "))
        await b.close()
asyncio.run(main())
