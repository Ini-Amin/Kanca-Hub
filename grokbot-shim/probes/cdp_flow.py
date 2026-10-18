"""AutoClaw login via CDP only (no xdotool): the captcha window is a CDP target."""
import asyncio, json, sys, urllib.parse
sys.path.insert(0, '/home/amen/Auto-FreeCF/scripts')
from playwright.async_api import async_playwright

rows = [json.loads(l) for l in open('/home/amen/.config/auto-freecf/zai_accounts.jsonl') if l.strip()]
a = rows[0]
CK = [{'name': p.split('=', 1)[0], 'value': p.split('=', 1)[1], 'domain': '.z.ai', 'path': '/'}
      for p in a['cookies'].split('; ') if '=' in p]
URLS = '/tmp/ac_opened_urls.txt'

async def main():
    async with async_playwright() as p:
        b = await p.chromium.connect_over_cdp("http://127.0.0.1:9444")

        async def pages():
            out = []
            for ctx in b.contexts:
                out.extend(ctx.pages)
            return out

        # 0. reset any half-finished attempt so the gate is at the login screen
        pg = next(x for x in await pages() if 'zwork.html' in x.url)
        for _ in range(4):
            body = await pg.evaluate("()=>document.body.innerText")
            if "Continue with Zai" in body and "Cancel" not in body:
                break
            for label in ("Cancel", "try again"):
                try:
                    await pg.get_by_text(label, exact=False).first.click(timeout=4000)
                    await pg.wait_for_timeout(2500)
                    break
                except Exception:
                    continue
            await pg.wait_for_timeout(1000)
        # 1. main window -> Continue with Zai
        await pg.get_by_text("Continue with Zai", exact=False).first.click(timeout=12000)
        print("1. clicked Continue with Zai", flush=True)

        # 2. wait for the captcha target, then tick it in-page
        cap = None
        for _ in range(30):
            await asyncio.sleep(1)
            for x in await pages():
                if 'Security' in (await x.title()) or 'data:text/html' in x.url:
                    cap = x; break
            if cap: break
        print("2. captcha target:", bool(cap), flush=True)
        if not cap: return

        for attempt in range(3):
            try:
                await cap.wait_for_timeout(2500)
                box = await cap.evaluate("""()=>{const e=document.getElementById('aliyunCaptcha-checkbox-icon');
                    if(!e) return null; const r=e.getBoundingClientRect();
                    return (r.width>0) ? {x:r.x+r.width/2, y:r.y+r.height/2} : null;}""")
                print("   checkbox box:", box, flush=True)
                if not box:
                    await cap.wait_for_timeout(2000); continue
                await cap.mouse.click(box['x'], box['y'])
                print("   clicked checkbox", flush=True)
            except Exception as e:
                print("   click err (window likely closed = solved):", str(e)[:60], flush=True)
                break

        # 3. capture the OAuth URL the app opened via xdg-open
        url = None
        for _ in range(30):
            await asyncio.sleep(1)
            try:
                u = open(URLS).read().strip()
                if u: url = u; break
            except FileNotFoundError: pass
        print("3. auth url captured:", bool(url), flush=True)
        if not url: return

        # 4. consent with the pool z.ai session
        b2 = await p.chromium.launch(
            executable_path='/home/amen/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome',
            headless=True, args=['--no-sandbox'])
        c2 = await b2.new_context(); await c2.add_cookies(CK)
        pg2 = await c2.new_page()
        await pg2.goto(url, wait_until='domcontentloaded', timeout=60000)
        await pg2.wait_for_timeout(2500)
        txt = await pg2.evaluate("()=>document.body.innerText.slice(0,120)")
        print("4. consent page:", txt.replace("\n", " ")[:110], flush=True)
        try:
            await pg2.locator('input[type=checkbox]').first.check(timeout=6000)
            await pg2.wait_for_timeout(600)
        except Exception as e:
            print("   chk:", str(e)[:40], flush=True)
        await pg2.get_by_role("button", name="Continue").first.click(timeout=10000)
        await pg2.wait_for_timeout(8000)
        print("5. redirect:", pg2.url[:70], flush=True)
        await b2.close()

asyncio.run(main())
