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
        await pg.wait_for_timeout(3000)
        # dump all clickable elements
        els = await pg.evaluate("""()=>[...document.querySelectorAll('button,input[type=checkbox],a,label')].map(e=>({
            tag:e.tagName, type:e.type||'', text:(e.innerText||e.getAttribute('aria-label')||'').trim().slice(0,50),
            disabled: e.disabled===true, checked: e.checked, cls:(e.className||'').toString().slice(0,40)
        })).slice(0,25)""")
        for e in els: print(json.dumps(e))
        await b.close()
asyncio.run(main())
