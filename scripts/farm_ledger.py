#!/usr/bin/env python3
"""
farm_ledger.py — a tiny, append-only ledger of farm runs.

Why: farming spans many commands (github/thk/grok/gmail/k12/autofarm) and the
same questions keep coming up — "what actually worked?", "which egress?", "where
did it stop?". This records one JSON line per attempt so you can audit across runs
without digging through terminal scrollback.

Shape (one JSON object per line, JSONL) at ~/.config/auto-freecf/farm_ledger.jsonl:
{
  "ts": "2026-10-06T12:00:00Z",
  "farm": "thk",              # github | thk | grok | gmail | k12 | autofarm | proxy
  "target": "tokenharbor.ai",
  "egress": "mobile",         # auto resolved source (mobile/pool_gateway/warp/.../direct/explicit)
  "exit_ip": "182.2.39.171",
  "stage": "created",         # free text: created | blocked:403 | phone_otp | ...
  "ok": true,
  "count": 1,                 # accounts/keys produced by this attempt
  "note": "",                 # short human note
}

Usage (CLI):
  python3 scripts/farm_ledger.py add --farm thk --stage created --ok --count 1 --egress mobile
  python3 scripts/farm_ledger.py tail -n 20
  python3 scripts/farm_ledger.py summary

As a library:
  from farm_ledger import record
  record("github", target="github.com/signup", egress="mobile", exit_ip=ip,
         stage="blocked:403", ok=False)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

LEDGER_PATH = Path(os.environ.get("FARM_LEDGER", Path.home() / ".config" / "auto-freecf" / "farm_ledger.jsonl"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def record(
    farm: str,
    *,
    target: str = "",
    egress: str = "",
    exit_ip: str = "",
    stage: str = "",
    ok: bool = False,
    count: int = 0,
    note: str = "",
    path: Path | str | None = None,
) -> dict:
    """Append one attempt to the ledger. Never raises (best-effort)."""
    entry = {
        "ts": _now(),
        "farm": str(farm or "").strip(),
        "target": str(target or "").strip(),
        "egress": str(egress or "").strip(),
        "exit_ip": str(exit_ip or "").strip(),
        "stage": str(stage or "").strip(),
        "ok": bool(ok),
        "count": int(count or 0),
        "note": str(note or "").strip(),
    }
    try:
        p = Path(path) if path else LEDGER_PATH
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - ledger must never break a farm
        pass
    return entry


def read(path: Path | str | None = None) -> list[dict]:
    p = Path(path) if path else LEDGER_PATH
    if not p.exists():
        return []
    out: list[dict] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    out.append(obj)
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        return []
    return out


def summarize(entries: list[dict]) -> str:
    """Human summary: per-farm ok/total, total accounts, top block stages."""
    if not entries:
        return "  (no farm runs recorded yet)"
    per_farm: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        per_farm[e.get("farm", "?")].append(e)

    lines = []
    lines.append(f"  {'farm':<10} {'runs':>5} {'ok':>4} {'accounts':>9}  last stage")
    total_accounts = 0
    for farm, rows in sorted(per_farm.items()):
        ok = sum(1 for r in rows if r.get("ok"))
        accts = sum(int(r.get("count") or 0) for r in rows)
        total_accounts += accts
        last = rows[-1].get("stage", "")
        lines.append(f"  {farm:<10} {len(rows):>5} {ok:>4} {accts:>9}  {last}")
    lines.append(f"  {'TOTAL':<10} {len(entries):>5} {sum(1 for r in entries if r.get('ok')):>4} {total_accounts:>9}")
    # top failure stages
    fails = Counter(r.get("stage", "?") for r in entries if not r.get("ok"))
    if fails:
        top = ", ".join(f"{k}×{v}" for k, v in fails.most_common(5))
        lines.append(f"  top block stages: {top}")
    return "\n".join(lines)


def _cli() -> int:
    ap = argparse.ArgumentParser(description="append-only farm run ledger")
    ap.add_argument("--path", default=None, help=f"ledger file (default {LEDGER_PATH})")
    sub = ap.add_subparsers(dest="cmd")

    a = sub.add_parser("add", help="record one attempt")
    a.add_argument("--farm", required=True)
    a.add_argument("--target", default="")
    a.add_argument("--egress", default="")
    a.add_argument("--exit-ip", default="")
    a.add_argument("--stage", default="")
    a.add_argument("--ok", action="store_true")
    a.add_argument("--count", type=int, default=0)
    a.add_argument("--note", default="")

    t = sub.add_parser("tail", help="show the last N entries")
    t.add_argument("-n", type=int, default=20)

    sub.add_parser("summary", help="summarize the ledger")

    args = ap.parse_args()
    path = args.path

    if args.cmd == "add":
        record(args.farm, target=args.target, egress=args.egress, exit_ip=args.exit_ip,
               stage=args.stage, ok=args.ok, count=args.count, note=args.note, path=path)
        print(f"  ✓ recorded {args.farm} [{args.stage or 'n/a'}] ok={args.ok}")
        return 0
    if args.cmd == "tail":
        rows = read(path)[-max(0, args.n):]
        for r in rows:
            flag = "✅" if r.get("ok") else "❌"
            print(f"  {flag} {r.get('ts','')} {r.get('farm',''):<9} egress={r.get('egress','') or '-':<12} "
                  f"stage={r.get('stage','') or '-':<16} n={r.get('count',0)}")
        return 0
    # default: summary
    print(summarize(read(path)))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
