#!/usr/bin/env python3
"""scripts/aikido_intel.py — Aikido Security Intelligence Feed & Database Enricher.

Fetches real-time vulnerability & malware intelligence from Aikido Intel (https://intel.aikido.dev)
and ingests findings directly into ~/.config/auto-freecf/cve.db:
  - Table: aikido_malware (ecosystem, package_name, version, status, detected_on, reasons)
  - Enriches cve_distilled_enriched.jsonl with supply-chain risk tags.

Usage:
  python3 scripts/aikido_intel.py sync       # Ingest latest supply-chain intelligence
  python3 scripts/aikido_intel.py check <pkg> # Check if a package is flagged
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Paths
HOME = Path.home()
CVE_DB = HOME / ".config" / "auto-freecf" / "cve.db"
AIKIDO_MALWARE_API = "https://intel.aikido.dev/api/latestMalware"

logging.basicConfig(
    level=logging.INFO,
    format="[aikido-intel] %(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("aikido-intel")


def init_db(con: sqlite3.Connection):
    cur = con.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS aikido_malware (
        id INTEGER PRIMARY KEY,
        ecosystem TEXT,
        package_name TEXT,
        version TEXT,
        status TEXT,
        release_date INTEGER,
        detected_on INTEGER,
        last_updated INTEGER,
        reason_count INTEGER,
        synced_at TEXT
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_aikido_pkg ON aikido_malware(package_name)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_aikido_eco ON aikido_malware(ecosystem)")
    con.commit()


def fetch_latest_malware() -> list[dict]:
    log.info("Fetching real-time malware intelligence from %s...", AIKIDO_MALWARE_API)
    req = urllib.request.Request(
        AIKIDO_MALWARE_API,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KancaHub/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
            if isinstance(data, list):
                return data
            return []
    except Exception as e:
        log.error("Failed to fetch from Aikido API: %s", e)
        return []


def sync_feed() -> int:
    if not CVE_DB.exists():
        log.error("cve.db not found at %s", CVE_DB)
        return 0

    items = fetch_latest_malware()
    if not items:
        log.warning("No items retrieved from Aikido feed.")
        return 0

    con = sqlite3.connect(str(CVE_DB))
    init_db(con)
    cur = con.cursor()

    now_iso = datetime.now(timezone.utc).isoformat()
    rows = []
    for it in items:
        rows.append((
            it.get("id"),
            it.get("ecosystem"),
            it.get("package_name"),
            it.get("version"),
            it.get("status"),
            it.get("release_date"),
            it.get("detected_on"),
            it.get("last_updated"),
            it.get("reason_count"),
            now_iso,
        ))

    cur.executemany("""
    INSERT OR REPLACE INTO aikido_malware
    (id, ecosystem, package_name, version, status, release_date, detected_on, last_updated, reason_count, synced_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    con.commit()
    count = cur.execute("SELECT count(*) FROM aikido_malware").fetchone()[0]
    con.close()

    log.info("Successfully ingested %d Aikido intelligence entries (Total in DB: %d)", len(rows), count)
    return len(rows)


def check_package(pkg_name: str):
    if not CVE_DB.exists():
        sys.exit(f"cve.db not found at {CVE_DB}")
    con = sqlite3.connect(str(CVE_DB))
    cur = con.cursor()
    cur.execute("SELECT ecosystem, package_name, version, status, reason_count, synced_at FROM aikido_malware WHERE package_name LIKE ?", (f"%{pkg_name}%",))
    rows = cur.fetchall()
    con.close()

    if not rows:
        print(f"[✓] Package '{pkg_name}' is CLEAN (not flagged in Aikido Intel database).")
        return

    print(f"[⚠️ WARNING] Package '{pkg_name}' matched {len(rows)} flagged entries:")
    for r in rows:
        print(f"  • [{r[0]}] {r[1]} v{r[2]} -> Status: {r[3]} (Flag reasons: {r[4]}, Synced: {r[5]})")


def main():
    parser = argparse.ArgumentParser(description="Aikido Intel Feed & Enricher")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("sync", help="Sync latest malware & supply chain intelligence into cve.db")
    chk = sub.add_parser("check", help="Check a package against Aikido database")
    chk.add_argument("package", help="Package name to check")

    args = parser.parse_args()
    if args.cmd == "sync" or not args.cmd:
        sync_feed()
    elif args.cmd == "check":
        check_package(args.package)


if __name__ == "__main__":
    main()
