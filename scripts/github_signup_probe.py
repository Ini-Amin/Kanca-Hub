#!/usr/bin/env python3
"""Probe github.com/signup through an HTTPS-CONNECT proxy with a real Camoufox browser.
Usage: github_signup_probe.py [--no-proxy | https://host:port]
Writes screenshot + HTML to /home/amen/Auto-FreeCF-vendor-refs/artifacts/github_probe/."""
import asyncio, json, os, sys

OUT="/home/amen/Auto-FreeCF-vendor-refs/artifacts/github_probe"
os.makedirs(OUT, exist_ok=True)

async def probe(proxy_url):
    from camoufox.async_api import AsyncCamoufox
    proxy={"server":proxy_url} if proxy_url and proxy_url!="--no-proxy" else None
    tag=(proxy_url.split("//")[1].replace(":","_").replace(".","_") if proxy else "direct")
    async with AsyncCamoufox(headless=True, proxy=proxy, humanize=True) as browser:
        page=await browser.new_page()
        res={"tag":tag}
        try:
            await page.goto("https://github.com/signup", timeout=60000, wait_until="domcontentloaded")
            await page.wait_for_timeout(8000)  # let DataDome JS run
            res["title"]=await page.title()
            res["url"]=page.url
            res["body"]=((await page.inner_text("body"))[:250]).replace("\n"," ")
            res["html_len"]=len(await page.content())
            res["email_field"]=await page.locator("input[name='email']").count()
            await page.screenshot(path=f"{OUT}/{tag}.png")
            open(f"{OUT}/{tag}.html","w").write(await page.content())
        except Exception as e:
            res["error"]=str(e)[:200]
        print(json.dumps(res, ensure_ascii=False))

if __name__=="__main__":
    asyncio.run(probe(sys.argv[1] if len(sys.argv)>1 else "--no-proxy"))
