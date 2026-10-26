#!/usr/bin/env python3
"""scripts/zcode_claim.py — auto-claim for ZCode plan coupons.

Extracted from the desktop app renderer (styles-C4MhEMbW.js), the real flow is:
  window.AliyunCaptchaConfig = {region:'sgp', prefix:'no8xfe'}
  load https://o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js
  initAliyunCaptcha({SceneId:'11xygtvd', mode:'popup', element:'#<id>',
                     button:'<sel>', success(param){...}})
  -> try instance.startTracelessVerification()   (no user interaction!)
  -> else click the button and solve the slider (captcha_slider.solve_aliyun)
  success callback receives captchaVerifyParam (string) directly.
Then POST /billing/claim with X-Aliyun-Captcha-Verify-Param.

Browser: CDP Chrome on :9222 when up (mint flow), else own headless camoufox.

usage: zcode_claim.py <email> <password> [plan_id]
"""
import asyncio, json, os, sys, urllib.request, urllib.error

sys.path.insert(0, "/home/amen/Auto-FreeCF/scripts")
import captcha_slider as CS
from playwright.async_api import async_playwright

CDP = "http://127.0.0.1:9222"
SDK = "https://o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js"
APP_KEY, SCENE, REGION = "no8xfe", "11xygtvd", "sgp"
# The real flow's origin is chat.z.ai/auth (create z.ai account -> zcode signup ->
# mint). zcode.z.ai/ 307-redirects to a marketing /cn|/en page, and the Aliyun
# verify param is bound to the page origin, so mounting the widget there was the
# likely cause of the 3007 (captcha verify failed) on claim. Override with
# ZC_CLAIM_ORIGIN if a future build moves the login back to zcode.z.ai.
CLAIM_ORIGIN = os.environ.get("ZC_CLAIM_ORIGIN", "https://chat.z.ai/auth")


def claim(jwt, plan_id, captcha_param, region="sgp"):
    H = {"Authorization": "Bearer " + jwt, "Content-Type": "application/json",
         "X-Aliyun-Captcha-Verify-Param": captcha_param,
         "X-Aliyun-Captcha-Verify-Region": region,
         "X-ZCode-App-Version": "3.16.3", "X-Platform": "linux-x64"}
    req = urllib.request.Request("https://zcode.z.ai/api/v1/zcode-plan/billing/claim",
                                 data=json.dumps({"plan_id": plan_id}).encode(),
                                 headers=H, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {"code": e.code}


async def main():
    email, pw, plan_id = sys.argv[1], sys.argv[2], (sys.argv[3] if len(sys.argv) > 3 else "zcode-v3-start-plan")
    jwt = open(os.path.expanduser("~/.zcode-bridge/jwt/%s.txt" % email)).read().strip()
    try:
        p = await async_playwright().start()
        b = await p.chromium.connect_over_cdp(CDP, timeout=4000)
    except Exception as e:
        # NOTE: plain chromium, NOT camoufox -- AliyunCaptcha.js refuses to
        # initialize inside camoufox's stealth patches (verified: initFn stays
        # undefined there, is a function instantly in plain chromium).
        print("[claim] CDP down (%s) -> headless chromium" % str(e)[:40], flush=True)
        p = await async_playwright().start()
        b = await p.chromium.launch(headless=True,
                                    executable_path=os.environ.get("ZC_CHROMIUM") or
                                    "/home/amen/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome")
    ctx = b.contexts[0] if b.contexts else await b.new_context()
    page = await ctx.new_page()
    try:
        rc = await _claim_page(page, jwt, plan_id)
    finally:
        # the watcher runs this on every tick; never leak the zcode.z.ai tab
        try:
            await page.close()
        except Exception:
            pass
    return rc


async def _claim_page(page, jwt, plan_id):
    # origin matters: the Aliyun verify param is bound to it. Use the real flow's
    # origin (chat.z.ai/auth) rather than zcode.z.ai/ (which 307s to a marketing
    # page and made the claim fail with code 3007).
    await page.goto(CLAIM_ORIGIN, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(1200)

    # mount the widget host exactly like the app does (renderer styles-*.js)
    await page.evaluate("""() => {
        window.AliyunCaptchaConfig = {region: 'sgp', prefix: 'no8xfe'};
        const host = document.createElement('div');
        host.id = 'captcha-host';
        const el = document.createElement('div');
        el.id = 'captcha-element';
        const btn = document.createElement('button');
        btn.id = 'captcha-btn';
        btn.innerText = 'verify';
        host.appendChild(el); host.appendChild(btn);
        document.body.appendChild(host);
    }""")
    loaded = await page.evaluate("""() => new Promise(res => {
        if (typeof window.initAliyunCaptcha === 'function') return res('already');
        const s = document.createElement('script');
        s.src = 'https://o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js';
        s.onload = () => res(typeof window.initAliyunCaptcha === 'function' ? 'ready' : 'loaded-but-no-init');
        s.onerror = () => res('load_fail');
        document.head.appendChild(s);
        setTimeout(() => res('timeout'), 30000);
    })""")
    print("[claim] SDK:", loaded, flush=True)
    if loaded not in ("ready", "already"):
        return 3

    init = await page.evaluate("""() => new Promise(res => {
        try {
            window.__captchaParam = null;
            window.initAliyunCaptcha({
                SceneId: '%s', mode: 'popup', showErrorTip: false,
                element: '#captcha-element', button: '#captcha-btn',
                getInstance: (inst) => { window.__ncInstance = inst; },
                success: (param) => { window.__captchaParam = param; },
                fail: (e) => { try { window.__captchaFail = JSON.stringify(e); } catch (_) { window.__captchaFail = String(e); } },
                onError: (e) => { window.__captchaErr = String((e && e.message) || e); },
            });
            setTimeout(() => res({init: 'ok',
                                  hasInst: typeof window.__ncInstance !== 'undefined',
                                  traceless: !!(window.__ncInstance && typeof window.__ncInstance.startTracelessVerification === 'function')}), 4000);
        } catch (e) { res({init: String(e)}); }
    })""" % (SCENE,))
    print("[claim] init:", init, flush=True)

    # the app's order: traceless first, interactive (slider) as fallback.
    # traceless is opt-in (ZC_TRACELESS=1): in headless it failed server-side
    # and may leave the widget in a half-passed state that confuses the slider.
    if init.get("traceless") and os.environ.get("ZC_TRACELESS") == "1":
        await page.evaluate("() => { try { window.__ncInstance.startTracelessVerification(); } catch(e) { window.__captchaErr = String(e); } }")
        for _ in range(20):
            await page.wait_for_timeout(1000)
            if await page.evaluate("() => !!window.__captchaParam"):
                break

    param = await page.evaluate("() => window.__captchaParam")
    if not param:
        # popup mode: nothing happens until the SDK's trigger button is clicked;
        # the puzzle dialog then appears for solve_aliyun.
        print("[claim] traceless gave no param -> interactive slider", flush=True)
        # The SDK (dynamicJS 3.29.0) only renders the widget inside z.ai's OWN
        # page context; a hand-mounted widget on a static page renders 0 nodes
        # and the trigger button never exists -> a hard crash here. Guard it.
        if not await page.evaluate("() => !!document.querySelector('#captcha-btn')"):
            print("[claim] widget did not mount (no trigger button)", flush=True)
            return 2
        try:
            await page.click("#captcha-btn", timeout=5000)
        except Exception as e:  # noqa: BLE001
            print("[claim] trigger click failed: %s" % str(e)[:80], flush=True)
            return 2
        await page.wait_for_timeout(2500)
        solved = await CS.solve_aliyun(page, puzzles=25)
        print("[claim] slider solved:", solved, flush=True)
        param = await page.evaluate("() => window.__captchaParam")

    print("[claim] fail/err:", await page.evaluate("() => [window.__captchaFail||null, window.__captchaErr||null]"), flush=True)
    if not param:
        print("[claim] no captchaVerifyParam recovered", flush=True)
        return 2
    print("[claim] param len:", len(param), flush=True)
    print("[claim] POST claim:", json.dumps(claim(jwt, plan_id, param))[:300], flush=True)
    return 0


sys.exit(asyncio.run(main()))
