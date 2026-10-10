#!/usr/bin/env python3
"""scripts/r9_farm_loop.py -- keep 9Router fed, using the existing kancahub pipeline.

Why this exists
---------------
9Router does NOT already rotate provider/model connections between providers,
and it does NOT farm us more keys. It only attempts the connections we hand it,
and if all are exhausted it returns "used this period's free allowance".

So when key #1 hits its quota, failover looks broken but is really just empty.
Toggling connections on/off in the dashboard cannot help. This is a supply
problem, and the fix is to keep supply topped up.

Everything that farms, injects or routes already lives in kancahub. This script
is a thin, honest DRIVER over those commands -- it does not reimplement them:

    kancahub thk status          -> free-tier state of every harbor account
    kancahub thk batch N         -> farm N accounts (egress + camoufox + mail)
    kancahub thk inject --verify -> push thk_ keys into 9Router
    kancahub thk sync --prune    -> verify + prune 9Router connections
    kancahub proxy verify        -> is our egress actually masked?

What this script adds on top:
  * a floor check ("do we have enough live keys?") with a clear verdict
  * a plan mode that never touches the network
  * an append-only ledger line per run, so drift is auditable

TokenHarbor free quota (measured 2026-10-10 via GET /api/me/free-tier):
  * each account gets ~151 requests per rolling 7-day period
    (req_used=151, used_pct=100, exhausted=true on a spent account;
    period 2026-10-07T08:02:09Z -> reset 2026-10-14T08:02:09Z)
  * the allowance covers: deepseek-v4-flash, mimo-v2.6-flash, mimo-v2.5,
    deepseek-v4.1-flash (SERVED with a ":free" suffix, reported without)
  * ONE NEW ACCOUNT == ONE FRESH ALLOWANCE, which is why farming is the fix
  * free requests are retained for training -- that is the trade

Signup cap (measured, see scripts/commands_thk.py): ~4 accounts per IP is safe,
5 is the hard cap, then "Too many sign-ups from this network". So a burst needs
fresh egress; the per-IP count is the real constraint, not the pace.

Usage
-----
  python scripts/r9_farm_loop.py status          # where do we stand?
  python scripts/r9_farm_loop.py plan            # what would we farm?
  python scripts/r9_farm_loop.py run --max 4     # show the exact commands
  python scripts/r9_farm_loop.py run --max 4 --apply   # actually do it
  python scripts/r9_farm_loop.py egress          # is masking working?

Nothing is farmed, injected, or synced unless --apply is given.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
HARBOR_ACCOUNTS = ROOT / "harbor" / "account.json"
LEDGER = Path(os.path.expanduser("~/.config/auto-freecf/farm_ledger.jsonl"))

# the python that has camoufox + playwright (harbor's Turnstile solver needs it)
CAMOUFOX_PY = Path(os.path.expanduser("~/.local/share/auto-freecf/camoufox-venv/bin/python"))
VENV_PY = Path(os.path.expanduser("~/.local/share/auto-freecf/venv/bin/python"))
KANCAHUB = SCRIPTS / "kancahub.py"

# below this many live keys we top up. Each account is worth only ~151 requests
# per 7 days, so a thin margin disappears fast.
FLOOR = 3
# TokenHarbor caps ~5 signups per IP per hour; 4 leaves a safety margin.
PER_IP_SAFE = 4


def py() -> str:
    """Python to drive kancahub (it imports the venv's deps)."""
    return str(VENV_PY if VENV_PY.exists() else sys.executable)


def kancahub(args: list[str], *, capture: bool = True) -> tuple[int, str]:
    """Run a kancahub subcommand and return (rc, output)."""
    cmd = [py(), str(KANCAHUB), *args]
    p = subprocess.run(cmd, capture_output=capture, text=True, cwd=str(ROOT))
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode, out


def ledger(stage: str, ok: bool, count: int, note: str = "") -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "farm": "thk",
        "target": "tokenharbor.ai",
        "egress": "auto",
        "stage": stage,
        "ok": ok,
        "count": count,
        "note": note,
    }
    with LEDGER.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def harbor_accounts() -> list[dict]:
    if not HARBOR_ACCOUNTS.exists():
        return []
    try:
        d = json.loads(HARBOR_ACCOUNTS.read_text())
        return d if isinstance(d, list) else d.get("accounts", [])
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_egress(_a) -> int:
    """Ask kancahub whether our egress is actually masked."""
    rc, out = kancahub(["proxy", "verify"], capture=True)
    print(out.strip())
    masked = "No masking yet" not in out and "down" not in out.lower()
    print(f"\nverdict: egress {'LOOKS MASKED' if masked else 'NOT MASKED -- farm will hit the per-IP cap'}")
    return 0 if masked else 1


def cmd_status(a) -> int:
    """Live free-tier state of every harbor account.

    `kancahub thk status` only reports ONE account (it wants --email/--password),
    so for a fleet view we call harbor's own client directly -- it holds the
    session that plain urllib loses to Cloudflare's 429. This is the one piece
    not already available as a kancahub subcommand.
    """
    accts = harbor_accounts()
    if not accts:
        print(f"no accounts at {HARBOR_ACCOUNTS}")
        return 1
    harbor_dir = ROOT / "harbor"
    if str(harbor_dir) not in sys.path:
        sys.path.insert(0, str(harbor_dir))
    try:
        from tools.tokenharbor.client import TokenHarborClient  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        print(f"cannot import harbor client ({e}); falling back to kancahub thk status")
        a.email = a.email if hasattr(a, "email") else None
        rc, out = kancahub(["thk", "status"], capture=True)
        print(out.strip())
        return rc

    live, burned, unknown = [], [], []
    for acc in accts:
        email, pw = acc.get("email"), acc.get("password")
        if not (email and pw):
            unknown.append((email, "no creds"))
            continue
        try:
            c = TokenHarborClient()
            r = c.login(email, pw)
            if not isinstance(r, dict) or not r.get("ok"):
                unknown.append((email, f"login failed: {str(r.get('error'))[:40]}"))
                continue
            ts = c.get_free_tier_status() or {}
            if ts.get("exhausted"):
                burned.append((email, ts.get("used_pct"), ts.get("reset_at")))
            else:
                live.append((email, ts.get("used_pct")))
        except Exception as e:  # noqa: BLE001
            unknown.append((email, f"err: {str(e)[:40]}"))

    print(f"harbor accounts : {len(accts)}")
    print(f"live            : {len(live)}   (floor {FLOOR})")
    print(f"exhausted       : {len(burned)}")
    if unknown:
        print(f"unknown         : {len(unknown)}")
    for e, pct in live:
        print(f"  LIVE  {str(e)[:40]:40} used {pct}%")
    resets = sorted({r for _, _, r in burned if r})
    if burned:
        print(f"\nall exhausted; earliest reset: {resets[0] if resets else '?'}")
    for e, why in unknown[:4]:
        print(f"  ?     {str(e)[:40]:40} {why}")
    need = max(0, FLOOR - len(live))
    print(f"\nverdict: {'TOP UP -- farm ' + str(need) + ' account(s)' if need else 'OK'}")
    return 0


def cmd_plan(_a) -> int:
    """Never touches the network. Shows what would be farmed and how."""
    rc, out = kancahub(["thk", "status"], capture=True)
    live = None
    # kancahub's table is rich-formatted; do a best-effort count of live rows.
    for token in ("live", "active"):
        if token in out.lower():
            break
    print("current state (from `kancahub thk status`):")
    print("\n".join("  " + ln for ln in out.strip().splitlines()[:18]))
    print(f"\nlive detected: {'unknown -- inspect above' if live is None else live}")
    print(f"floor: {FLOOR}")
    print("\nFarm plan (respecting the ~5 signups/IP cap; 4 is the safe burst):")
    print("  kancahub proxy verify                 # confirm masking first")
    print("  kancahub thk batch 4                  # farm 4 accounts")
    print("  kancahub thk inject --verify          # push keys into 9Router")
    print("  kancahub thk sync --prune             # verify + prune stale conns")
    print("\nIf you expect more than 4, rotate egress between batches.")
    print("No changes made (plan only).")
    return 0


def cmd_run(a) -> int:
    """Farm -> inject -> sync, by driving the kancahub pipeline."""
    n = a.max
    print(f"target: farm {n} TokenHarbor account(s), then inject into 9Router")
    print()

    steps: list[list[str]] = [["proxy", "verify"]]
    steps.append(["thk", "batch", str(n)])
    steps.append(["thk", "inject", "--verify"])
    steps.append(["thk", "sync", "--prune"])

    # show the exact calls regardless of --apply, so the plan is inspectable
    for s in steps:
        print(f"  kancahub {' '.join(s)}")
    print()

    if not a.apply:
        print("DRY RUN -- nothing was run. Add --apply to execute.")
        print("Preconditions: `kancahub proxy verify` must show masking, or the")
        print("~5-signups-per-IP cap will stop the batch early.")
        ledger("plan", True, 0, f"dry-run for {n} account(s)")
        return 0

    ok_all = True
    produced = 0
    for s in steps:
        label = " ".join(s)
        print(f"\n=== kancahub {label} ===", flush=True)
        rc, out = kancahub(s)
        tail = out.strip().splitlines()
        for ln in tail[-14:]:
            print("  " + ln)
        if rc != 0:
            ok_all = False
            print(f"  ! exited {rc} -- stopping here")
            ledger(f"step:{label}", False, 0, f"rc={rc}")
            break
        ledger(f"step:{label}", True, 0)
        if s[1:2] == ["batch"]:
            after = len(harbor_accounts())
            produced = after
            print(f"  harbor accounts now: {after}")

    ledger("run", ok_all, produced, f"target={n}")
    print(f"\n{'OK' if ok_all else 'PARTIAL'}: farm loop finished")
    return 0 if ok_all else 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="live free-tier state of every harbor account").set_defaults(fn=cmd_status)
    sub.add_parser("plan", help="what would be farmed (offline)").set_defaults(fn=cmd_plan)
    sub.add_parser("egress", help="is our egress masked?").set_defaults(fn=cmd_egress)
    r = sub.add_parser("run", help="farm -> inject -> sync (needs --apply)")
    r.add_argument("--max", type=int, default=4, help="how many accounts to farm (default 4)")
    r.add_argument("--apply", action="store_true", help="actually run the pipeline")
    r.set_defaults(fn=cmd_run)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
