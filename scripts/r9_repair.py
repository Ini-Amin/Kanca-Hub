#!/usr/bin/env python3
"""scripts/r9_repair.py -- make 9Router key-level failover actually work.

Run `r9_conncheck.py` first (writes /tmp/opencode/r9_health.json), then this.

What it fixes:
  1. PRIORITY TIES  -- connections with equal priority have no deterministic
     order, so "next key" is arbitrary. Renumber 1..N per provider,
     healthiest first (ok > unknown > http*), preserving relative order.
  2. DEAD KEYS      -- connections the probe proved dead are marked
     testStatus=unavailable + lastError so 9Router deprioritizes them and
     the *stored* state finally matches reality.
  3. STALE COMBOS   -- a combo entry pointing at a model the provider no
     longer serves (e.g. THK/glm-5.2:free) 404s and poisons the whole chain.
     Move unknown entries to the end instead of deleting them.
  4. isActive=0     -- a provider with ZERO active connections returns
     "No active credentials" instead of falling back. Re-activate the best
     probe-ok connection if a provider has none active but has a live key.

Safety: writes a timestamped backup of data.sqlite first, and never invents
credentials -- it only reorders/flags what is already stored.

Usage:
  python scripts/r9_repair.py --dry-run     # show the plan (default)
  python scripts/r9_repair.py --apply
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

DB = Path(os.path.expanduser("~/.9router/db/data.sqlite"))
HEALTH = Path("/tmp/opencode/r9_health.json")
THK = "openai-compatible-chat-1d39647b-193d-4f65-b38b-03d80c92460a"

# model names that THK no longer serves (probed live 2026-10-10)
THK_STALE = {"glm-5.2:free", "glm-5.2"}

# ranking: a live key beats an unknown one beats a known-dead one
RANK = {"ok": 0, "stored": 1, "unprobeable": 1, "error": 2}


def rank(result: str) -> int:
    if result.startswith("http"):
        return 3 if result in ("http402", "http403", "http401") else 2
    return RANK.get(result, 2)


def load_health():
    if not HEALTH.exists():
        raise SystemExit(
            f"missing {HEALTH} -- run: python scripts/r9_conncheck.py"
        )
    return {r["id"]: r for r in json.loads(HEALTH.read_text())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="show plan only (default)")
    args = ap.parse_args()
    apply = args.apply and not args.dry_run

    health = load_health()
    con = sqlite3.connect(str(DB))
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    conns = [dict(r) for r in cur.execute("SELECT * FROM providerConnections")]
    by_prov: dict[str, list[dict]] = {}
    for c in conns:
        by_prov.setdefault(c["provider"], []).append(c)

    changes: list[str] = []

    # ---- 1 + 2: per-provider ordering and dead-key flagging -----------------
    new_prio: dict[str, int] = {}
    new_state: dict[str, dict] = {}

    for prov, lst in by_prov.items():
        # keep existing relative order as the tiebreak, healthier first
        lst_sorted = sorted(
            lst,
            key=lambda c: (
                rank((health.get(c["id"]) or {}).get("result", "stored")),
                c["priority"] if c["priority"] is not None else 999,
                c["createdAt"] or "",
            ),
        )
        for i, c in enumerate(lst_sorted, start=1):
            if c["priority"] != i:
                new_prio[c["id"]] = i
                changes.append(
                    f"[priority] {prov[:34]:34} {str(c['name'])[:22]:22} "
                    f"{c['priority']} -> {i}"
                )
        # flag dead keys so stored state == reality
        for c in lst:
            h = health.get(c["id"]) or {}
            res = h.get("result")
            if res and res.startswith("http") and h.get("http") in (401, 402, 403):
                try:
                    d = json.loads(c["data"]) if c["data"] else {}
                except Exception:
                    d = {}
                if d.get("testStatus") != "unavailable":
                    new_state[c["id"]] = {
                        "testStatus": "unavailable",
                        "errorCode": h["http"],
                        "lastError": f"[{h['http']}]: {h.get('detail', '')}",
                        "lastErrorAt": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
                        "_raw": d,
                    }
                    changes.append(
                        f"[deadkey]  {prov[:34]:34} {str(c['name'])[:22]:22} "
                        f"http{h['http']} -> unavailable"
                    )

    # ---- 3: stale combo entries -> move to the end -------------------------
    combos = [dict(r) for r in cur.execute("SELECT id, name, models FROM combos")]
    combo_updates: dict[str, list[str]] = {}
    for cb in combos:
        try:
            models = json.loads(cb["models"]) if cb["models"] else []
        except Exception:
            continue
        stale, fresh = [], []
        for m in models:
            tail = m.split("/", 1)[-1]
            if m.startswith("THK/") and tail in THK_STALE:
                stale.append(m)
            else:
                fresh.append(m)
        if stale:
            combo_updates[cb["id"]] = fresh + stale
            changes.append(
                f"[combo]    {cb['name']:14} moved {len(stale)} stale to end: {stale}"
            )

    # ---- 4: provider with 0 active but a live key --------------------------
    react: dict[str, int] = {}
    for prov, lst in by_prov.items():
        if any(c["isActive"] for c in lst):
            continue
        best = next(
            (c for c in lst if (health.get(c["id"]) or {}).get("result") == "ok"),
            None,
        )
        if best:
            react[best["id"]] = 1
            changes.append(
                f"[revive]   {prov[:34]:34} {str(best['name'])[:22]:22} isActive 0 -> 1"
            )

    # ---- report ------------------------------------------------------------
    print(f"connections: {len(conns)}  providers: {len(by_prov)}  changes: {len(changes)}")
    for c in changes:
        print("  " + c)
    if not changes:
        print("  (nothing to do)")
        return

    if not apply:
        print("\nDRY RUN -- re-run with --apply to write")
        return

    bak = DB.with_suffix(f".sqlite.bak-{int(time.time())}")
    shutil.copy2(DB, bak)
    print(f"\nbackup -> {bak}")

    for cid, p in new_prio.items():
        cur.execute(
            "UPDATE providerConnections SET priority=? WHERE id=?", (p, cid)
        )
    for cid, st in new_state.items():
        raw = st.pop("_raw")
        raw.update(st)
        cur.execute(
            "UPDATE providerConnections SET data=? WHERE id=?",
            (json.dumps(raw), cid),
        )
    for cid, active in react.items():
        cur.execute(
            "UPDATE providerConnections SET isActive=? WHERE id=?", (active, cid)
        )
    for cbid, models in combo_updates.items():
        cur.execute(
            "UPDATE combos SET models=?, updatedAt=? WHERE id=?",
            (json.dumps(models), time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()), cbid),
        )
    con.commit()
    print(f"applied {len(changes)} changes")


if __name__ == "__main__":
    main()
