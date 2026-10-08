#!/usr/bin/env python3
"""
scripts/farm_report.py — READ-ONLY farm status table.

Reads whatever exists and prints one summary table; never writes, never
imports kancahub:

  (a) harbor/account.json           -> live thk keys
  (b) ~/.9router/db/data.sqlite     -> providerConnections by provider,
                                       active count + testStatus error/unavailable
  (c) ~/.config/auto-freecf/sms_latest.json -> last SMS code + age
  (d) ~/.config/auto-freecf/otp_webhook/public_url.txt -> webhook public URL

Usage:
  python scripts/farm_report.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

HOME = Path.home()
AUTO = HOME / "Auto-FreeCF"
CFG = HOME / ".config" / "auto-freecf"
NINE_DB = HOME / ".9router" / "db" / "data.sqlite"
HARBOR = AUTO / "harbor" / "account.json"
SMS_LATEST = CFG / "sms_latest.json"
WEBHOOK_URL = CFG / "otp_webhook" / "public_url.txt"

BAD_TEST_STATUS = ("error", "unavailable")

# ── pure helpers (unit-tested, no I/O) ───────────────────────────────

def count_live_thk(accounts) -> int:
    """Count harbor accounts holding a live thk key (api_key/key starting thk_live_)."""
    if not isinstance(accounts, list):
        accounts = accounts.get("accounts", []) if isinstance(accounts, dict) else []
    return sum(1 for a in accounts
               if str((a or {}).get("api_key") or (a or {}).get("key") or "").startswith("thk_live_"))

def provider_summary(rows) -> dict:
    """Summarize providerConnections rows: (provider, isActive, data_json)."""
    by_provider: dict[str, int] = {}
    active = errors = total = 0
    for provider, is_active, data in rows:
        total += 1
        by_provider[provider] = by_provider.get(provider, 0) + 1
        if is_active:
            active += 1
        try:
            status = (json.loads(data or "{}") or {}).get("testStatus")
        except Exception:
            status = None
        if status in BAD_TEST_STATUS:
            errors += 1
    return {"total": total, "active": active, "errors": errors, "by_provider": by_provider}

def format_sms(code, ts_ms, now_ms) -> str:
    """'112233  (age 12s ago)' — age is unknown when ts is missing/implausible."""
    if not code:
        return "none"
    if not ts_ms:
        return f"{code}  (age unknown)"
    age_s = max(0.0, (now_ms - ts_ms) / 1000.0)
    if age_s < 90:
        age = f"{int(age_s)}s"
    elif age_s < 5400:
        age = f"{int(age_s // 60)}m"
    else:
        age = f"{age_s / 3600:.1f}h"
    return f"{code}  ({age} ago)"

def format_table(*, thk_live, providers, sms, webhook) -> list[str]:
    """Render the summary as plain lines (deterministic, order-stable)."""
    lines = ["── farm status ──────────────────────────────",
             f"  harbor thk live keys : {thk_live}"]
    if providers is None:
        lines.append("  9router providers    : unavailable")
    else:
        lines.append(f"  9router providers    : {providers['total']} total, "
                     f"{providers['active']} active, {providers['errors']} testStatus error/unavailable")
        for name, n in sorted(providers["by_provider"].items()):
            lines.append(f"      {name:32} {n}")
    lines.append(f"  last SMS             : {sms}")
    lines.append(f"  otp webhook          : {webhook}")
    return lines

# ── readers (best-effort, read-only) ─────────────────────────────────

def read_harbor_live(path: Path = HARBOR) -> int:
    try:
        return count_live_thk(json.loads(path.read_text()))
    except Exception:
        return 0

def read_providers(db: Path = NINE_DB):
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT provider, isActive, data FROM providerConnections").fetchall()
        finally:
            con.close()
        return provider_summary(rows)
    except Exception:
        return None

def read_sms(path: Path = SMS_LATEST, now_ms: float | None = None) -> str:
    try:
        d = json.loads(path.read_text())
        return format_sms(d.get("code"), d.get("ts"), now_ms if now_ms is not None else time.time() * 1000)
    except Exception:
        return "none"

def read_webhook(path: Path = WEBHOOK_URL) -> str:
    try:
        url = path.read_text().strip()
        return url or "down"
    except Exception:
        return "down"

def main(argv: list[str] | None = None) -> int:
    for line in format_table(
        thk_live=read_harbor_live(),
        providers=read_providers(),
        sms=read_sms(),
        webhook=read_webhook(),
    ):
        print(line)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())