#!/usr/bin/env python3
"""
scripts/lock_dead_models.py — stop the router routing to models that are GONE.

WHY THIS EXISTS
---------------
`r9_modelcheck.py` finds out which models answer; this acts on it. A dead model
is not merely useless, it is expensive: measured 2026-10-19, `ocz/space-bunny-free`
returned 410 in 1.3s while `THK/claude-haiku-5.5:free` burned a 45s timeout.

9router's fast-failure mechanism is a per-connection `modelLock_<model>` holding
an expiry timestamp: a locked model is skipped, so the request returns at once
instead of waiting on a dead upstream.

TWO RULES THAT THIS SCRIPT EXISTS TO GET RIGHT
----------------------------------------------
1. Only DEFINITIVE failures are lockable. A timeout or 5xx means "busy", and
   `THK/claude-haiku-5.5:free` is a LIMITED-TIME free promo that fails by
   timeout -- locking it for days would hide a model that recovers by itself.
   Definitive = HTTP 400/404/410/501 ("deprecated", "model is unavailable").

2. A lock must be SCOPED to the provider that failed. The router matches the
   PROVIDER's model name, so the lock key drops the catalog prefix -- and two
   providers can own the same provider-side name. Locking the dead
   `nr/nemotron-3-ultra-free` with a bare `modelLock_nemotron-3-ultra-free`
   written on every connection also blocked the HEALTHY
   `ocz/nemotron-3-ultra-free` (measured: it went from 200 to 503 in 0.01s
   across 510 connection locks). So a lock is written only on connections whose
   `providerSpecificData.prefix` matches, and any bare key found on a
   non-matching connection is cleared as a repair.

Merge rule: an existing lock is only ever EXTENDED, never shortened, so this
cannot undo the long-lived paid-model locks from lock_thk_paid.py.

Usage:
  python scripts/lock_dead_models.py --dry-run     # preview
  python scripts/lock_dead_models.py               # apply (default 7-day TTL)
  python scripts/lock_dead_models.py --unlock      # clear (scoped)
  python scripts/lock_dead_models.py --include-transient   # also lock timeouts (not advised)
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB = Path(os.environ.get("NINE_ROUTER_DB", Path.home() / ".9router" / "db" / "data.sqlite"))
HEALTH = Path(os.environ.get("R9_MODEL_HEALTH", Path.home() / ".config" / "auto-freecf" / "r9_model_health.json"))

# "This model does not exist" -- safe to lock. Everything else is treated as busy.
PERMANENT_STATUSES = {400, 404, 410, 501}


def classify(path: Path, min_attempts: int) -> tuple[list[str], list[str]]:
    """(definitively dead, failed-but-not-definitively) from the health snapshot."""
    d = json.loads(path.read_text())
    dead, transient = [], []
    for model, r in d.get("results", {}).items():
        if r.get("ok"):
            continue
        if int(r.get("attempts", 1)) < min_attempts:   # only trust a retried verdict
            continue
        (dead if int(r.get("status") or 0) in PERMANENT_STATUSES else transient).append(model)
    return sorted(dead), sorted(transient)


def split_id(model: str) -> tuple[str | None, str]:
    """`THK/claude-haiku-5.5:free` -> ("THK", "claude-haiku-5.5:free")."""
    return tuple(model.split("/", 1)) if "/" in model else (None, model)


def conn_prefix(data: dict) -> str | None:
    """The provider a connection belongs to; the scope key for its locks."""
    return (data.get("providerSpecificData") or {}).get("prefix")


def parse_ts(v) -> datetime | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--health", default=str(HEALTH))
    ap.add_argument("--ttl-days", type=float, default=7.0, help="how long a dead model stays locked (default 7)")
    ap.add_argument("--min-attempts", type=int, default=2,
                    help="only trust failures retried this many times (default 2)")
    ap.add_argument("--include-transient", action="store_true",
                    help="ALSO lock timeouts/5xx/429 — hides temporarily-busy and "
                         "limited-time promo models (e.g. THK/claude-haiku-5.5:free)")
    ap.add_argument("--unlock", action="store_true", help="clear this tool's locks (scoped)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    health, db_path = Path(a.health), Path(a.db)
    if not health.exists():
        print(f"x no health snapshot at {health} (run: kancahub 9router models check)", file=sys.stderr)
        return 1
    if not db_path.exists():
        print(f"x no router DB at {db_path}", file=sys.stderr)
        return 1

    dead, transient = classify(health, a.min_attempts)
    lockable = dead + (transient if a.include_transient else [])
    checked = json.loads(health.read_text()).get("checked_at", "?")

    if transient and not a.include_transient:
        print(f"{len(transient)} model(s) failed but NOT definitively (timeout/5xx) -- left unlocked:")
        print("  " + ", ".join(transient[:8]) + (" ..." if len(transient) > 8 else ""))
    if not lockable and not a.unlock:
        print("nothing definitive to lock")
        return 0

    # bare provider-side name -> the set of prefixes judged dead for it
    dead_by_bare: dict[str, set[str | None]] = {}
    for m in lockable:
        prov, bare = split_id(m)
        dead_by_bare.setdefault(bare, set()).add(prov)

    now = datetime.now(timezone.utc)
    until = now + timedelta(days=a.ttl_days)
    cleared = now - timedelta(seconds=1)

    db = sqlite3.connect(db_path)
    rows = db.execute("select id, name, data from providerConnections").fetchall()
    if not rows:
        print("x no providerConnections", file=sys.stderr)
        return 1

    touched = repaired = 0
    for cid, name, data in rows:
        try:
            j = json.loads(data or "{}")
        except json.JSONDecodeError:
            continue
        pfx = conn_prefix(j)
        changed = False
        for bare, provs in dead_by_bare.items():
            key = "modelLock_" + bare
            cur = parse_ts(j.get(key))
            belongs = (None in provs) or (pfx in provs)
            if a.unlock:
                if belongs and cur is not None:
                    j[key] = cleared.isoformat()
                    changed = True
                continue
            if belongs:
                if cur is None or cur < until:
                    j[key] = until.isoformat()
                    changed = True
            elif cur is not None:
                # A bare lock on a provider we did NOT judge dead: our earlier
                # unscoped write, or a collision. Clear it (repair).
                j[key] = cleared.isoformat()
                changed = True
                repaired += 1
        if changed:
            touched += 1
            if not a.dry_run:
                db.execute("update providerConnections set data=?, updatedAt=? where id=?",
                           (json.dumps(j), now.isoformat(), cid))
    if not a.dry_run:
        db.commit()

    verb = "unlocked" if a.unlock else "locked"
    print(f"snapshot {checked}: {len(lockable)} lockable model(s), "
          f"{verb} on {touched}/{len(rows)} connections"
          + (f"; cleared {repaired} mis-scoped lock(s)" if repaired else ""))
    if lockable:
        print("  " + ", ".join(lockable[:10]) + (" ..." if len(lockable) > 10 else ""))
    if a.dry_run:
        print("dry run: nothing written")
    else:
        print("apply with: systemctl restart blu-9router.service")
    return 0


if __name__ == "__main__":
    sys.exit(main())