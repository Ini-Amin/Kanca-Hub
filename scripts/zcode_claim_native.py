#!/usr/bin/env python3
"""
scripts/zcode_claim_native.py — claim the ZCode Start Plan WITHOUT the desktop app.

Reverse-engineered from ZCode 3.14.5 (app.asar/out/host/index.js):

  1. GET  /api/v1/client/configs?app_version=<v>&platform=<os>-<arch>
          -> data.configs.captcha {enabled, region, prefix, sceneId, skip_model_request}
  2. load the Aliyun SDK with window.<X>Config = {region, prefix} and
     init<X>({SceneId, mode:'popup', element, button, getInstance, success, fail, onError})
     -> solve -> success(param)  = the SIGNED, page-bound captchaVerifyParam
  3. POST /api/v1/zcode-plan/billing/claim
        headers: Authorization: Bearer <zcodeJwt>
                 X-Aliyun-Captcha-Verify-Param: <param>
                 X-Aliyun-Captcha-Verify-Region: <region>
                 X-ZCode-App-Version: <ver>
                 X-Platform: <os>-<arch>
        body:    {"plan_id":"zcode-v3-start-plan"}

No app install: it's just HTTP + a browser for the captcha. This is the "bridge".
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

ZCODE = "https://zcode.z.ai"
APP_VERSION = "3.14.5"
PLAN_ID = "zcode-v3-start-plan"
CFG_URL = f"{ZCODE}/api/v1/client/configs?app_version={APP_VERSION}&platform=linux-x64"
CLAIM_URL = f"{ZCODE}/api/v1/zcode-plan/billing/claim?app_version={APP_VERSION}"


def _req(url, jwt, method="GET", body=None, extra=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if jwt:
        req.add_header("Authorization", f"Bearer {jwt}")
    for k, v in (extra or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:  # noqa: BLE001
            return {"code": e.code}


def get_captcha_config(jwt):
    d = _req(CFG_URL, jwt)
    return (d.get("data") or {}).get("configs", {}).get("captcha")


# The app's own SDK init. Order matters: set window.AliyunCaptchaConfig BEFORE the
# SDK script loads, then initAliyunCaptcha({SceneId, mode, element, button, ...}).
INJECT = """(scene) => new Promise((res, rej) => {
  window.AliyunCaptchaConfig = { region: scene.region, prefix: scene.prefix };
  const doInit = () => {
    if (typeof window.initAliyunCaptcha !== 'function') return rej('no initAliyunCaptcha');
    const el = document.createElement('div'); el.id='__capbox';
    el.style.cssText='position:fixed;left:0;top:0;width:0;height:0;overflow:visible;z-index:2147483647';
    const btn = document.createElement('button'); btn.id='__capbtn'; btn.type='button';
    btn.style.cssText='position:fixed;left:50%;top:50%;width:1px;height:1px;opacity:0;border:0';
    document.body.appendChild(el); document.body.appendChild(btn);
    window.__capParam = null;
    try {
      window.initAliyunCaptcha({
        SceneId: scene.sceneId, mode: 'popup', language: 'en', showErrorTip: false,
        element: '#__capbox', button: '#__capbtn',
        getInstance: (inst) => { window.__capInst = inst;
          try { inst.show(); } catch (e) { try { btn.click(); } catch (e2) {} }
          res(true); },
        success: (p) => { window.__capParam = p; },
        fail: (e) => { window.__capErr = e; },
        onError: (e) => { window.__capErr = e; }
      });
    } catch (e) { rej(String(e)); }
  };
  if (typeof window.initAliyunCaptcha === 'function') return doInit();
  const s = document.createElement('script');
  s.src = 'https://o.alicdn.com/captcha-frontend/aliyunCaptcha/AliyunCaptcha.js';
  s.onload = doInit; s.onerror = () => rej('script load failed');
  document.head.appendChild(s);
})"""


async def solve_param(cfg, log=print):
    """Open the zcode captcha with the SERVER config and return the signed param."""
    import captcha_slider as CS
    from camoufox.async_api import AsyncCamoufox
    async with AsyncCamoufox(headless=False, humanize=True, os="windows") as browser:
        page = await browser.new_page()
        await page.goto("https://chat.z.ai/", wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(3000)
        try:
            await page.evaluate(INJECT, {"region": cfg["region"], "prefix": cfg["prefix"], "sceneId": cfg["sceneId"]})
        except Exception as e:  # noqa: BLE001
            log(f"  init err: {str(e)[:120]}")
        await page.wait_for_timeout(2500)
        # our slider solver drives the widget
        await CS.solve_aliyun(page, puzzles=20, log=log)
        for _ in range(30):
            p = await page.evaluate("()=>window.__capParam")
            if p:
                return p
            await page.wait_for_timeout(1000)
        return None


def claim(jwt, param, region):
    extra = {"X-Aliyun-Captcha-Verify-Param": param, "X-ZCode-App-Version": APP_VERSION,
             "X-Platform": "linux-x64"}
    if region:
        extra["X-Aliyun-Captcha-Verify-Region"] = region
    return _req(CLAIM_URL, jwt, method="POST", body={"plan_id": PLAN_ID}, extra=extra)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Claim ZCode Start Plan without the app")
    ap.add_argument("--jwt-file", default="/tmp/opencode/zc_jwt.txt")
    ap.add_argument("--jwt", default=None)
    ap.add_argument("--param", default=None, help="pre-solved captchaVerifyParam")
    a = ap.parse_args(argv)
    jwt = a.jwt
    if not jwt and Path(a.jwt_file).exists():
        jwt = Path(a.jwt_file).read_text().strip()
    if not jwt:
        print("need --jwt or --jwt-file"); return 1
    cfg = get_captcha_config(jwt)
    print("captcha config:", cfg)
    param = a.param or asyncio.run(solve_param(cfg))
    print("param:", (param or "")[:60])
    if not param:
        print("no captcha param"); return 1
    r = claim(jwt, param, (cfg or {}).get("region"))
    print("CLAIM:", json.dumps(r)[:300])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())