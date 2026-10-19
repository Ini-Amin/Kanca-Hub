"""Lock TokenHarbor's paid models on every THK connection in 9router.

WHY: THK advertises 70 models but our 31 keys sit at $0 balance, so only the
4 ':free' ids can ever answer. A request for e.g. THK/claude-opus-5.5 walks the
whole key pool (measured: 47 s, 31 x HTTP 402) before failing. That is the same
fan-out-across-accounts pattern that got the Zcode pool throttled.

9router supports per-connection `modelLock_<model>` keys holding an expiry
timestamp; a locked model is skipped for that connection. We set them for every
paid THK id, far in the future, so a paid request fails fast instead of
hammering 31 keys. Free ids stay unlocked.

Idempotent. Re-run after adding THK keys.
"""
import json
import sqlite3
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

DB = "/root/.9router/db/data.sqlite"
LOCK_YEARS = 5


def main() -> int:
    db = sqlite3.connect(DB)
    rows = db.execute(
        "select id, data from providerConnections where name like 'th_%'"
    ).fetchall()
    if not rows:
        print("no THK connections", file=sys.stderr)
        return 1

    # One key is enough to enumerate the catalog.
    first = json.loads(rows[0][1])
    req = urllib.request.Request(
        "https://tokenharbor.ai/v1/models",
        headers={"authorization": "Bearer " + first["apiKey"]},
    )
    catalog = json.load(urllib.request.urlopen(req, timeout=30))
    paid = [m["id"] for m in catalog["data"] if not m["id"].endswith(":free")]
    free = [m["id"] for m in catalog["data"] if m["id"].endswith(":free")]
    until = (datetime.now(timezone.utc) + timedelta(days=365 * LOCK_YEARS)).isoformat()
    print(f"catalog: {len(catalog['data'])} models | free {len(free)} | paid {len(paid)}")

    updated = 0
    for cid, data in rows:
        j = json.loads(data)
        changed = False
        for m in paid:
            k = "modelLock_" + m
            if j.get(k) != until:
                j[k] = until
                changed = True
        # A paid model must never be the default for this connection.
        if j.get("defaultModel") not in free:
            j["defaultModel"] = "deepseek-v4.1-flash:free"
            changed = True
        if changed:
            db.execute(
                "update providerConnections set data=?, updatedAt=? where id=?",
                (json.dumps(j), datetime.now(timezone.utc).isoformat(), cid),
            )
            updated += 1
    db.commit()
    print(f"locked {len(paid)} paid models on {updated}/{len(rows)} connections")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
