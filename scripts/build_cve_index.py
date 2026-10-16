#!/usr/bin/env python3
"""Build a queryable SQLite index over the local NVD JSON feeds + CWE XML.

ONE script, ONE home for the index. Run on the LAPTOP (data lives here),
never on blu. Re-runnable: rebuilds from scratch each time (fresh git pull
first: cd ~/nvd-json-data-feeds && git pull).

  python3 scripts/build_cve_index.py [--db PATH]

Schema:
  cve(id, year, description, cvss31, cvss40, severity, cwes, vendors, published, modified)
  cwe(id, name, description)
  meta(key, value)   -- built_at, cve_count, cwe_count, feeds_commit

Query examples (sqlite3 ~/.config/auto-freecf/cve.db):
  SELECT id,cvss31,severity,substr(description,1,120) FROM cve
    WHERE description LIKE '%WordPress%' AND cvss31 >= 9 ORDER BY cvss31 DESC LIMIT 5;
  SELECT * FROM cwe WHERE name LIKE '%SQL Injection%';
"""
import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

HOME = Path.home()
NVD = HOME / "nvd-json-data-feeds"
CWE_XML = HOME / "cwec_v4.20.xml"
DEFAULT_DB = HOME / ".config" / "auto-freecf" / "cve.db"


def cve_rows():
    """Yield dicts from every NVD JSON file (NVD 2.0 API format:
    top-level id/vulnStatus/descriptions/metrics/weaknesses/configurations).
    Skips Rejected entries."""
    files = sorted(NVD.rglob("CVE-*.json"))
    for f in files:
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        cid = d.get("id", "")
        if not cid or not cid.startswith("CVE-"):
            continue
        if (d.get("vulnStatus") or "") == "Rejected":
            continue
        descs = [x.get("value", "") for x in (d.get("descriptions") or [])
                 if x.get("lang", "").startswith("en")]
        desc = descs[0] if descs else ""
        cvss31 = cvss40 = None
        sev = ""
        metrics = d.get("metrics") or {}
        for x in (metrics.get("cvssMetricV31") or []):
            cd = x.get("cvssData") or {}
            if cd.get("baseScore") is not None:
                cvss31 = float(cd["baseScore"])
                sev = (cd.get("baseSeverity") or sev).upper()
                break
        for x in (metrics.get("cvssMetricV40") or []):
            cd = x.get("cvssData") or {}
            if cd.get("baseScore") is not None:
                cvss40 = float(cd["baseScore"])
                break
        cwes = []
        for w in (d.get("weaknesses") or []):
            for x in (w.get("description") or []):
                v = (x.get("value") or "").strip()
                if v.startswith("CWE-") and v not in cwes:
                    cwes.append(v)
        vendors = []
        for cfg in (d.get("configurations") or []):
            for node in (cfg.get("nodes") or []):
                for m in (node.get("cpeMatch") or []):
                    if not m.get("vulnerable", True):
                        continue
                    parts = (m.get("criteria") or "").split(":")
                    if len(parts) >= 5 and parts[3] and parts[3] != "*":
                        v = parts[3]
                        if v not in vendors:
                            vendors.append(v)
                    if len(vendors) >= 5:
                        break
        try:
            year = int(cid.split("-")[1])
        except Exception:
            year = 0
        yield (cid, year, desc, cvss31, cvss40, sev, ",".join(cwes),
               ",".join(vendors[:5]), d.get("published", ""),
               d.get("lastModified", ""))


def cwe_rows():
    ns = {"c": "http://cwe.mitre.org/cwe-7"}
    t = ET.parse(str(CWE_XML))
    for w in t.getroot().findall(".//c:Weakness", ns):
        wid = "CWE-" + (w.get("ID") or "")
        name = w.get("Name") or ""
        d = w.find("c:Description", ns)
        yield (wid, name, (d.text or "") if d is not None else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    a = ap.parse_args()
    dbp = Path(a.db)
    dbp.parent.mkdir(parents=True, exist_ok=True)
    if dbp.exists():
        dbp.unlink()
    t0 = time.time()
    cx = sqlite3.connect(str(dbp))
    cx.execute("""CREATE TABLE cve(id TEXT PRIMARY KEY, year INT, description TEXT,
        cvss31 REAL, cvss40 REAL, severity TEXT, cwes TEXT, vendors TEXT,
        published TEXT, modified TEXT)""")
    cx.execute("CREATE TABLE cwe(id TEXT PRIMARY KEY, name TEXT, description TEXT)")
    cx.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(NVD),
                                capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        commit = "?"
    cx.execute("INSERT INTO meta VALUES('feeds_commit', ?)", (commit,))
    cx.execute("INSERT INTO meta VALUES('built_at', ?)",
               (time.strftime("%Y-%m-%dT%H:%M:%S"),))
    n = 0
    batch = []
    for r in cve_rows():
        batch.append(r)
        if len(batch) >= 2000:
            cx.executemany("INSERT OR IGNORE INTO cve VALUES(?,?,?,?,?,?,?,?,?,?)", batch)
            n += len(batch)
            batch = []
            if n % 50000 == 0:
                print("  ... %d CVE" % n, flush=True)
    if batch:
        cx.executemany("INSERT OR IGNORE INTO cve VALUES(?,?,?,?,?,?,?,?,?,?)", batch)
        n += len(batch)
    cx.execute("INSERT INTO meta VALUES('cve_count', ?)", (str(n),))
    m = 0
    cb = []
    for r in cwe_rows():
        cb.append(r)
    cx.executemany("INSERT OR IGNORE INTO cwe VALUES(?,?,?)", cb)
    m = len(cb)
    cx.execute("INSERT INTO meta VALUES('cwe_count', ?)", (str(m),))
    cx.execute("CREATE INDEX idx_cve_year ON cve(year)")
    cx.execute("CREATE INDEX idx_cve_cvss ON cve(cvss31)")
    cx.execute("CREATE VIRTUAL TABLE IF NOT EXISTS cve_fts USING fts5(id, description)")
    cx.execute("INSERT INTO cve_fts(id, description) SELECT id, description FROM cve")
    cx.commit()
    sz = dbp.stat().st_size
    print("DONE: %d CVE + %d CWE in %.0fs -> %s (%.1f MB)" % (n, m, time.time() - t0, dbp, sz / 1e6))


if __name__ == "__main__":
    sys.exit(main())
