#!/usr/bin/env python3
"""
Watch Tencent Cloud Free Tier Lighthouse stock (act 11135, 2C2G 3 months).

Polls the public, no-login quota endpoint once per run (cron/timer calls it every
10 min — never a tight loop). Logs every reading to a JSONL so the refill time
can be learned, and alerts once when cycle stock opens. Claiming itself needs a
logged-in, verified Tencent account in a real browser; this script never submits
an order.

  python3 scripts/tencent_lighthouse_watch.py            # one poll
  NTFY_TOPIC=my-secret-topic python3 ...                 # also push to ntfy.sh
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

URL = "https://act-api-intl.tencentcloud.com/dianshi/calc-goods-quota?uin=&csrfCode="
GOODS = {11135: "Lighthouse 2C2G 3m", 11134: "CVM S5.MEDIUM4 3m"}
LOG = Path.home() / ".config" / "auto-freecf" / "tencent_lighthouse.jsonl"
CLAIM_URL = "https://www.tencentcloud.com/act/pro/FreeTier"


def poll(goods_id: int) -> dict:
    req = urllib.request.Request(URL, data=json.dumps({"goods_id_list": [goods_id], "preview": 0}).encode(),
                                 headers={"content-type": "application/json",
                                          "origin": "https://www.tencentcloud.com", "referer": CLAIM_URL,
                                          "user-agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)["data"]["goods_quota_list"][0]


def is_open(q: dict) -> bool:
    """Open when total stock remains and this cycle's slots (if any) aren't used up."""
    return q.get("available", 0) > 0 and q.get("cycle_available", 1) > 0


def alert(msg: str) -> None:
    print(msg)
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        try:
            urllib.request.urlopen(urllib.request.Request(
                f"https://ntfy.sh/{topic}", data=msg.encode(), headers={"Click": CLAIM_URL}), timeout=15)
        except Exception as e:
            print("ntfy failed:", e)


def main() -> int:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    prev = {}
    if LOG.exists():
        for line in LOG.read_text().splitlines()[-len(GOODS):]:
            try:
                r = json.loads(line); prev[r["goods_id"]] = r
            except Exception:
                pass
    for gid, name in GOODS.items():
        try:
            q = poll(gid)
        except Exception as e:
            print(f"{name}: poll failed ({e})"); continue
        rec = {"t": int(time.time()), "goods_id": gid, **q, "open": is_open(q)}
        with LOG.open("a") as f:
            f.write(json.dumps(rec) + "\n")
        print(f"{name}: available={q.get('available')} cycle={q.get('cycle_available')}/{q.get('cycle_total')}")
        if rec["open"] and not prev.get(gid, {}).get("open"):
            alert(f"Tencent {name} free slot OPEN ({q.get('cycle_available')} left) — claim: {CLAIM_URL}")
    return 0


if __name__ == "__main__":
    assert is_open({"available": 5, "cycle_available": 1}) and not is_open({"available": 5, "cycle_available": 0})
    assert is_open({"available": 5}) and not is_open({"available": 0})
    sys.exit(main())
