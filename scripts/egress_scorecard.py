#!/usr/bin/env python3
"""scripts/egress_scorecard.py -- grade every egress route against the sites we farm.

Why this exists
---------------
We kept rediscovering the same thing the hard way: an egress that works for one
target is refused by the next. A Cloudflare-fronted target tolerates IPs that
Google reCAPTCHA calls "automated queries", and vice versa. So grade each route
against each *kind* of gate, not against a single target.

The gates that actually matter to us
------------------------------------
  tokenharbor   Cloudflare-fronted signup/login (page + API)
  recaptcha     Google reCAPTCHA v2 audio challenge (Webshare/HProxy signup)
  cloudflare    generic Cloudflare-fronted page (workers, etc.)

Verdicts
--------
  OK        the page loaded and no challenge was served
  CHALLENGE Cloudflare interstitial ("Just a moment...")
  RECAPTCHA Google refused the audio challenge ("automated queries")
  RATELIMIT HTTP 429 from our own probing (transient, not a ban)
  DENIED    HTTP 402/403/404
  FAIL      egress unusable (connection error / timeout)

Measured 2026-10-10 (see docs/EGRESS_SCORECARD.md for the write-up):

  route                        tokenharbor        recaptcha
  direct 182.8.255.126         OK (after cooldown) RECAPTCHA  (hosting:true)
  WARP 104.28.219.241          OK                  RECAPTCHA  (proxy:true)
  TinyFish residential         OK                  OK         <-- only clean one
  monosans free lists          9-22% alive, too      untested (timeouts)
                               slow for a browser

So: WARP buys us TokenHarbor but NOT Google. Free lists are too flaky to drive a
browser. Only real residential clears everything -- and a new user has none,
which is exactly why the mobile-tether path (a real carrier IP) is the one that
matters.

Usage
-----
  python scripts/egress_scorecard.py                    # score current egress
  python scripts/egress_scorecard.py --probe 104.28.1.2 # score an explicit IP
  python scripts/egress_scorecard.py --json             # machine-readable
"""
from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# the targets, and what "good" looks like for each
TARGETS = {
    "tokenharbor": "https://tokenharbor.ai/login",
    "tokenharbor_api": "https://tokenharbor.ai/api/me/free-tier",
    "cloudflare": "https://workers.cloudflare.com/",
    "webshare": "https://proxy.webshare.io/register",
}

_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def _open(url: str, timeout: float = 30.0):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=timeout, context=_ctx)


def classify(status: int | None, body: str, err: str | None) -> str:
    if err:
        if "429" in err:
            return "RATELIMIT"
        for code in ("402", "403", "404", "503"):
            if code in err:
                return "DENIED"
        return "FAIL"
    low = body.lower()
    # A real Cloudflare interstitial has a distinctive shape. Do NOT match
    # `/cdn-cgi/challenge-platform/...` on its own -- every Cloudflare site
    # injects that bootstrap script, so matching it flagged healthy pages as
    # CHALLENGE. Look for the interstitial itself.
    if (
        "just a moment" in low
        or "cf-mitigated" in low
        or "enable javascript and cookies to continue" in low
        or ("attention required" in low and "cloudflare" in low)
        or "__cf_chl_" in low
    ):
        return "CHALLENGE"
    if "automated queries" in low:
        return "RECAPTCHA"
    if status in (401,):
        return "OK"  # proper auth gate = the page itself is reachable
    if status and status >= 500:
        return "FAIL"
    if status and status >= 400:
        return "DENIED" if status in (402, 403, 404) else "RATELIMIT"
    return "OK"


def _probe_once(url: str, timeout: float) -> dict:
    try:
        r = _open(url, timeout)
        body = r.read(200_000).decode(errors="replace")
        return {"status": r.status, "verdict": classify(r.status, body, None)}
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read(200_000).decode(errors="replace")
        except Exception:  # noqa: BLE001
            pass
        # A 401 is *success* for our purpose: it means the endpoint answered and
        # the gate is auth, not a WAF. classify() handles that, but only if we
        # do not route it through the error branch's 4xx mapping first.
        if e.code == 401:
            return {"status": e.code, "verdict": classify(e.code, body, None)}
        return {"status": e.code, "verdict": classify(e.code, body, str(e))}
    except Exception as e:  # noqa: BLE001
        return {"status": None, "verdict": classify(None, "", str(e))}


def probe_one(url: str, timeout: float, attempts: int = 3,
              pace: float = 4.0) -> dict:
    """Probe with retries, paced.

    Cloudflare serves an interstitial intermittently -- the same egress can get
    CHALLENGE once and OK twice in a row, and *hammering* is what provokes it
    (paced probes: 5/5 clean; rapid probes: 3/3 challenged). So retry slowly,
    keep the best of N, and record the tally so the flakiness stays visible.
    """
    import time

    seen: list[dict] = []
    for i in range(attempts):
        r = _probe_once(url, timeout)
        seen.append(r)
        if r["verdict"] == "OK":
            break
        if i + 1 < attempts:
            time.sleep(pace)
    best = next((r for r in seen if r["verdict"] == "OK"), seen[-1])
    best["probes"] = [s["verdict"] for s in seen]
    return best


def current_ip(timeout: float = 20.0) -> str:
    for u in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            return _open(u, timeout).read().decode().strip()[:45]
        except Exception:  # noqa: BLE001
            continue
    return "?"


def ip_reputation(ip: str, timeout: float = 20.0) -> dict:
    try:
        r = _open(f"http://ip-api.com/json/{ip}?fields=query,country,isp,as,"
                  f"mobile,proxy,hosting", timeout)
        return json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:60]}


def recaptcha_check(timeout: float = 240.0) -> str:
    """Ask Google for the Webshare audio challenge and report the verdict.

    Needs camoufox (browser). Returns OK / RECAPTCHA / SKIP.
    """
    import subprocess
    from pathlib import Path

    cam = Path.home() / ".local" / "share" / "auto-freecf" / "camoufox-venv" / "bin" / "python"
    if not cam.exists():
        return "SKIP (no camoufox venv)"
    js = r'''
import asyncio
from camoufox.async_api import AsyncCamoufox
async def main():
    async with AsyncCamoufox(headless=True, humanize=True, os="windows") as b:
        pg = await b.new_page()
        await pg.goto("https://proxy.webshare.io/register",
                      wait_until="domcontentloaded", timeout=60000)
        await pg.wait_for_timeout(4000)
        for lbl in ("Sign Up With Email", "Sign Up"):
            el = pg.get_by_text(lbl, exact=False)
            if await el.count():
                try:
                    await el.first.click(timeout=4000)
                except Exception:
                    pass
                break
        await pg.wait_for_timeout(5000)
        for f in pg.frames:
            if "bframe" in f.url:
                await f.evaluate(
                    "() => { const b=document.getElementById('recaptcha-audio-button');"
                    " if(b) b.click(); }")
                await pg.wait_for_timeout(6000)
                t = await f.evaluate(
                    "() => (document.body?document.body.innerText:'').replace(/\\n/g,' ')")
                if "automated queries" in t.lower():
                    print("RECAPTCHA")
                elif "audio" in t.lower() or await f.evaluate(
                        "() => !!document.getElementById('audio-source')"):
                    print("OK")
                else:
                    print("UNKNOWN: " + t[:70])
                return
        print("OK")   # no challenge served at all = anchor auto-passed
asyncio.run(main())
'''
    try:
        p = subprocess.run([str(cam), "-c", js], capture_output=True, text=True,
                           timeout=timeout)
        out = (p.stdout or "").strip().splitlines()
        return out[-1] if out else "SKIP (no output)"
    except Exception as e:  # noqa: BLE001
        return f"SKIP ({str(e)[:40]})"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", help="explicit egress IP to look up (reputation only)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--recaptcha", action="store_true",
                    help="also run the slow Google reCAPTCHA browser check")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--pace", type=float, default=8.0,
                    help="seconds between retries (hammering provokes the challenge)")
    a = ap.parse_args()

    ip = a.probe or current_ip(a.timeout)
    rep = ip_reputation(ip, a.timeout)

    results = {"ip": ip, "reputation": rep, "targets": {}}
    for name, url in TARGETS.items():
        results["targets"][name] = {
            "url": url, **probe_one(url, a.timeout, pace=a.pace)}

    if a.recaptcha and not a.probe:
        results["recaptcha"] = recaptcha_check()

    if a.json:
        print(json.dumps(results, indent=1))
        return 0

    flags = []
    if rep.get("hosting"):
        flags.append("hosting")
    if rep.get("proxy"):
        flags.append("proxy")
    if rep.get("mobile"):
        flags.append("mobile")
    print(f"\n  egress IP : {ip}")
    print(f"  isp       : {rep.get('isp', rep.get('error', '?'))}")
    print(f"  flags     : {', '.join(flags) if flags else 'clean'}")
    print("\n  target                     verdict")
    print("  " + "-" * 44)
    for name, r in results["targets"].items():
        print(f"  {name:24}   {r['verdict']}")
    if "recaptcha" in results:
        print(f"\n  google reCAPTCHA audio : {results['recaptcha']}")

    # the honest bottom line: can this egress carry a signup?
    t = results["targets"]
    hard_block = any(v["verdict"] in ("CHALLENGE", "RECAPTCHA", "FAIL", "DENIED")
                     for v in t.values())
    print(f"\n  verdict: {'BLOCKED somewhere -- not safe for signups' if hard_block else 'USABLE for these targets'}")
    return 0 if not hard_block else 1


if __name__ == "__main__":
    sys.exit(main())
