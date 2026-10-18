import asyncio, json
from playwright.async_api import async_playwright
async def main():
    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp("http://127.0.0.1:9444")
        pg = b.contexts[0].pages[0]
        print("URL:", pg.url[:140])
        print("TEXT:", (await pg.evaluate("()=>document.body.innerText")).replace("\n"," | ")[:500])
        await pg.screenshot(path="/tmp/main_now.png")
        await b.close()
asyncio.run(main())
