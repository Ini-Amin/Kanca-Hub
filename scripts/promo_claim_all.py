#!/usr/bin/env python3
"""Claim the 1024 Dev Day coupon for every farmed account.

Three things have to be right at once, each of which alone looks like a plain
"blocked" and sends you chasing the wrong cause:

1. `x-device-mid` must be UNIQUE PER ACCOUNT. The desktop app ships one machine
   id and the first version of this used it for all 225 accounts, so z.ai saw a
   single device logging into 225 accounts and hid the campaign entirely --
   `/billing/preview` returned `plans: []` and the coupon was invisible.
   Device ids are derived with UUIDv5 from the email (see zcode.device_mid).

2. The request must NOT come from the home IP. Direct answers 3012 "unusual
   activity" on the claim endpoint; the HK relay answers `code: 0`.

3. The claim must carry the SAME device id that preview did, plus the full
   desktop header set. Sending a bare header set makes claim 3012 while preview
   still looks fine, which reads as an IP problem but is not.

Each account yields a one-shot plan: 300M GLM-5.3-Flash + 200M GLM-5.3.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zcode  # noqa: E402

PLAN = "zcode-v3-start-plan-1024"
RELAY = os.environ.get("CLAIM_RELAY", "http://127.0.0.1:25346")
ACCOUNTS = os.path.expanduser("~/.zcode-bridge/accounts.txt")
LOG = os.path.expanduser("~/.config/auto-freecf/logs/promo_claim.log")
GAP = float(os.environ.get("CLAIM_GAP", "2"))

_opener = urllib.request.build_opener(urllib.request.ProxyHandler({"https": RELAY}))


def log(msg: str) -> None:
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a") as fh:
        fh.write(line + "\n")


def accounts() -> list[str]:
    return [l.split(":")[0] for l in open(ACCOUNTS).read().splitlines() if l.strip()]


def claimed_units(email: str) -> int:
    try:
        d = zcode.billing(email, "balance")
    except Exception:
        return 0
    return max([b.get("remaining_units") or 0 for b in d.get("balances") or []] or [0])


def claim(email: str) -> dict:
    jwt = zcode.jwt_for(email)
    if not jwt:
        return {"code": "no-jwt"}
    headers = {
        "Authorization": "Bearer " + jwt,
        "Content-Type": "application/json",
        "User-Agent": "ZCode/3.16.3",
        "http-referer": "https://zcode.z.ai",
        "x-zcode-app-version": "3.16.3",
        "x-release-channel": "production",
        "x-client-language": "en-US",
        "x-client-timezone": "Asia/Jakarta",
        "x-os-category": "linux",
        "x-os-version": "7.2.8",
        "x-title": "Z Code@electron",
        "x-platform": "linux-x64",
        "x-device-mid": zcode.device_mid(email),
        "x-request-id": "00000000-0000-4000-8000-%012d" % (time.time() % 1e12),
    }
    req = urllib.request.Request(
        "https://zcode.z.ai/api/v1/zcode-plan/billing/claim?app_version=3.16.3",
        data=json.dumps({"plan_id": PLAN}).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with _opener.open(req, timeout=25) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read())
        except Exception:
            return {"code": e.code}
    except Exception as e:
        return {"code": "ERR", "msg": str(e)[:60]}


def main() -> int:
    accs = accounts()
    log("=== start: %d accounts, relay %s ===" % (len(accs), RELAY))
    ok = already = nocoupon = fail = 0
    for i, em in enumerate(accs, 1):
        if claimed_units(em) >= 200_000_000:
            already += 1
            log("[%d/%d] had   %s" % (i, len(accs), em))
            continue
        try:
            plans = zcode.promo_preview(em)
        except Exception as e:
            fail += 1
            log("[%d/%d] prev-fail %s %s" % (i, len(accs), em, type(e).__name__))
            continue
        if not plans:
            nocoupon += 1
            log("[%d/%d] none  %s" % (i, len(accs), em))
            time.sleep(GAP)
            continue
        r = claim(em)
        code = r.get("code")
        if code == 0:
            ok += 1
            log("[%d/%d] CLAIM  %s" % (i, len(accs), em))
        else:
            fail += 1
            log("[%d/%d] FAIL  %s code=%s %s" % (i, len(accs), em, code, str(r.get("msg"))[:40]))
            if code == 3012:
                time.sleep(15)
        time.sleep(GAP)
    log("=== done: claimed=%d already=%d no-coupon=%d failed=%d ===" % (ok, already, nocoupon, fail))
    return 0


if __name__ == "__main__":
    sys.exit(main())
