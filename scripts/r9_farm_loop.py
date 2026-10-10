#!/usr/bin/env python3
"""scripts/r9_farm_loop.py -- Option C: the autofarm loop that keeps 9Router fed.

The problem this solves
-----------------------
9Router's failover is *not* broken. When THK key #1 hits its quota, the router
does try key #2, #3 ... #14 -- but if every key is exhausted, the best it can
report is "used this period's free allowance". Toggling connections on/off in
the dashboard changes nothing, because there is no quota to fall back to.

That is a *supply* problem, and this loop fixes supply:

    probe  ->  detect providers below the floor  ->  farm more accounts
           ->  inject into 9Router  ->  re-probe  ->  repair ordering

TokenHarbor free quota (measured 2026-10-10):
  * `:free` models have a per-account rolling ~7-day allowance
    ("You've used this period's free allowance. Your next rolling 7-day
    period starts on <date>"), separate from the paid wallet balance.
  * So ONE NEW ACCOUNT == ONE FRESH FREE ALLOWANCE. That is why farming works.
  * Free models that count: deepseek-v4.1-flash:free, mimo-v2.6-flash:free,
    claude-haiku-5.5:free (among others).
  * Free requests are retained for training -- that is the trade for free access.

Signup limits (measured, see scripts/commands_thk.py):
  * ~4 accounts per IP is safe; 5 is the hard cap, then
    "Too many sign-ups from this network. Please try again in an hour."
  * So a burst needs fresh egress; the per-IP cap is the real constraint,
    not the pace.

Usage
-----
  python scripts/r9_farm_loop.py status                 # health of all THK keys
  python scripts/r9_farm_loop.py plan                   # what it would farm
  python scripts/r9_farm_loop.py run --max 4            # farm up to 4 accounts
  python scripts/r9_farm_loop.py run --max 4 --apply    # ...and inject them

Nothing is farmed or injected unless --apply is given.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
HARBOR = ROOT / "harbor" / "account.json"
DB = Path(os.path.expanduser("~/.9router/db/data.sqlite"))
HEALTH = Path("/tmp/opencode/r9_health.json")
LEDGER = Path(os.path.expanduser("~/.config/auto-freecf/farm_ledger.jsonl"))

THK_PROVIDER = "openai-compatible-chat-1d39647b-193d-4f65-b38b-03d80c92460a"
# Ground truth from GET /api/me/free-tier (allowance_model_labels). The API
# reports these WITHOUT a ":free" suffix, but chat requests need the suffix.
THK_FREE_MODELS = [
    "deepseek-v4-flash:free",
    "mimo-v2.6-flash:free",
    "mimo-v2.5:free",
    "deepseek-v4.1-flash:free",
]
# Each account gets ~151 requests per rolling 7-day period (see req_used/used_pct).
# below this many live keys for a provider -> top up
FLOOR = 3
# safe accounts per egress IP before TokenHarbor's network cap bites
PER_IP_SAFE = 4


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def load_accounts() -> list[dict]:
    if not HARBOR.exists():
        return []
    try:
        d = json.loads(HARBOR.read_text())
        return d if isinstance(d, list) else d.get("accounts", [])
    except Exception:
        return []


def free_tier_status(email: str, password: str) -> dict | None:
    """Ask TokenHarbor for this account's free-tier state.

    GET /api/me/free-tier returns exhausted / used_pct / reset_at without
    spending a free request, so it is the cheap sensor. Plain urllib is
    Cloudflare-challenged (429 "Just a moment..."), so we go through the
    harbor client, which already holds a working session.
    """
    try:
        harbor = ROOT / "harbor"
        if str(harbor) not in sys.path:
            sys.path.insert(0, str(harbor))
        from tools.tokenharbor.client import TokenHarborClient  # noqa: PLC0415

        c = TokenHarborClient()
        r = c.login(email, password)
        if not isinstance(r, dict) or r.get("error"):
            return None
        ts = c.get_free_tier_status()
        return ts if isinstance(ts, dict) and "exhausted" in ts else None
    except Exception:  # noqa: BLE001
        return None


def probe_key(api_key: str, model: str) -> tuple[bool, str]:
    """True if this key still has free allowance for `model`."""
    import urllib.error
    import urllib.request

    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 4,
    }).encode()
    req = urllib.request.Request(
        "https://tokenharbor.ai/v1/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30):
            return True, "ok"
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read().decode()).get("error", {}) or {}
            msg = err.get("message", "") or ""
            typ = err.get("type", "") or ""
        except Exception:
            msg, typ = "", ""
        low = msg.lower()
        if "free allowance" in low:
            return False, "quota_used"
        if "balance is at $0" in low or "balance_zero" in typ:
            return False, "paid_wallet_zero"
        # model not served for free (stale/renamed free route) -- try the next model
        if e.code == 404 or "not found" in low or "free_route_inactive" in low:
            return False, "model_unavailable"
        if e.code == 429 or "too many" in low:
            return False, "rate_limited"
        return False, f"http{e.code}"


def status() -> dict:
    accts = load_accounts()
    live, dead = [], []
    for a in accts:
        email, pw, key = a.get("email"), a.get("password"), a.get("api_key")
        if not key:
            continue
        # prefer the cheap, no-request-spent check
        ts = free_tier_status(email, pw) if (email and pw) else None
        if ts and "exhausted" in ts:
            if not ts.get("exhausted"):
                live.append({
                    "email": email, "key": key[:18],
                    "why": f"live:{100 - (ts.get('used_pct') or 0)}% left",
                    "reset_at": ts.get("reset_at"),
                })
            else:
                dead.append({
                    "email": email, "key": key[:18],
                    "why": f"quota_used:{ts.get('used_pct')}%",
                    "used": ts.get("req_used"), "reset_at": ts.get("reset_at"),
                })
            continue
        # fall back to spending one small request
        ok, why = False, "unknown"
        for m in THK_FREE_MODELS:
            ok, why = probe_key(key, m)
            if ok:
                why = f"ok:{m}"
                break
            if why in ("model_unavailable", "rate_limited"):
                continue
            break
        if not ok and why in ("model_unavailable", "rate_limited"):
            why = "all_free_models_unavailable"
        (live if ok else dead).append({"email": email, "key": key[:18], "why": why})
    return {
        "accounts": len(accts),
        "live": live,
        "dead": dead,
        "live_count": len(live),
        "floor": FLOOR,
        "need": max(0, FLOOR - len(live)),
        "resets": sorted({d.get("reset_at") for d in dead if d.get("reset_at")}),
    }


def ledger(farm: str, stage: str, ok: bool, count: int, note: str = "") -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "farm": farm, "target": "tokenharbor.ai",
        "egress": "auto", "exit_ip": "",
        "stage": stage, "ok": ok, "count": count, "note": note,
    }
    with LEDGER.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def cmd_status(_args) -> int:
    s = status()
    print(f"TokenHarbor accounts : {s['accounts']}")
    print(f"live free allowance  : {s['live_count']}  (floor={s['floor']})")
    print(f"used / no allowance  : {len(s['dead'])}")
    if s["live"]:
        print("\nLIVE:")
        for e in s["live"]:
            print(f"  {str(e['email'])[:34]:34} {e['key']}...")
    if s["dead"]:
        print("\nBURNED / UNUSABLE:")
        for e in s["dead"][:8]:
            print(f"  {str(e['email'])[:34]:34} {e['key']}...  {e['why']}")
        if len(s["dead"]) > 8:
            print(f"  ... and {len(s['dead']) - 8} more")
    why_counts: dict[str, int] = {}
    for e in s["dead"]:
        why_counts[e["why"].split(":")[0]] = why_counts.get(e["why"].split(":")[0], 0) + 1
    if why_counts:
        print("\nreasons: " + ", ".join(f"{k}={v}" for k, v in sorted(why_counts.items(), key=lambda x: -x[1])))
    if s["need"]:
        if why_counts.get("quota_used"):
            print("\nverdict: TOP UP NEEDED -- free allowance spent on "
                  f"{why_counts['quota_used']} account(s); farm new ones or wait for the reset date.")
        else:
            print("\nverdict: no live free allowance detected -- check model names / egress.")
    else:
        print("\nverdict: OK")
    return 0


def cmd_plan(_args) -> int:
    s = status()
    print(f"live={s['live_count']} floor={FLOOR} -> need {s['need']} new account(s)")
    if s["need"]:
        print(f"\nFarm plan (respecting ~{PER_IP_SAFE} signups/IP before the network cap):")
        left = s["need"]
        egress = 1
        while left > 0:
            n = min(PER_IP_SAFE, left)
            print(f"  egress #{egress}: {n} account(s)  (then rotate IP -- TokenHarbor caps ~5/IP/hr)")
            left -= n
            egress += 1
    print("\nNo changes made (plan only).")
    return 0


def cmd_run(args) -> int:
    s = status()
    need = args.max if args.max is not None else s["need"]
    if need <= 0:
        print(f"live={s['live_count']} >= floor={FLOOR}; nothing to farm.")
        return 0
    print(f"live={s['live_count']} floor={FLOOR} -> farming {need} account(s)")

    cmd = [sys.executable, str(SCRIPTS / "autofarm.py"),
           "https://tokenharbor.ai/signup", "--domain", args.domain]
    if args.apply:
        cmd.append("--inject-9router")
    print("+ " + " ".join(cmd))
    if not args.apply:
        print("\nDRY RUN -- add --apply to actually farm and inject.")
        print("Note: needs working egress (mobile IP). TokenHarbor caps ~5 signups/IP/hr.")
        return 0

    p = run(cmd, cwd=str(ROOT))
    tail = (p.stdout or "").strip().splitlines()[-12:]
    for line in tail:
        print("  " + line)
    ok = p.returncode == 0
    ledger("thk", "farm_batch", ok, need, f"rc={p.returncode}")
    print(f"\nautofarm rc={p.returncode}")

    if ok:
        print("\nre-probing...")
        s2 = status()
        print(f"live now: {s2['live_count']}")

    # keep the router's stored ordering/flags in sync with reality
    if HEALTH.exists() or True:
        print("\n+ r9_conncheck.py")
        run([sys.executable, str(SCRIPTS / "r9_conncheck.py"), "--workers", "10"], cwd=str(ROOT))
        print("+ r9_repair.py --apply")
        r = run([sys.executable, str(SCRIPTS / "r9_repair.py"), "--apply"], cwd=str(ROOT))
        for line in (r.stdout or "").strip().splitlines()[-6:]:
            print("  " + line)
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="probe every THK key's free allowance").set_defaults(fn=cmd_status)
    sub.add_parser("plan", help="what it would farm").set_defaults(fn=cmd_plan)
    r = sub.add_parser("run", help="farm accounts (needs --apply to act)")
    r.add_argument("--max", type=int, default=None, help="how many accounts to farm")
    r.add_argument("--domain", default="kancalabs.my.id", help="mailbox domain to sign up under")
    r.add_argument("--apply", action="store_true", help="actually farm and inject")
    r.set_defaults(fn=cmd_run)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
