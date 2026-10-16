#!/usr/bin/env python3
"""Extract the exact upstream model ID from the newest requestDetails rows.
Usage: python3 scripts/extract_upstream_model.py [N]
Shows, per row: client model -> upstream model actually sent to Google,
plus endpoint hints. Built for the argon MITM capture (2026-10-16)."""
import json
import os
import sqlite3
import sys

db = os.path.expanduser("~/.9router/db/data.sqlite")
c = sqlite3.connect(db)
n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
rows = c.execute(
    "SELECT timestamp, status, provider, model, data FROM requestDetails "
    "ORDER BY rowid DESC LIMIT ?", (n,)).fetchall()
for ts, st, prov, m, data in reversed(rows):
    d = json.loads(data)
    print("== %s  %-7s  %-14s  client-model=%s" % (ts[11:19], st, (prov or "")[:14], m))
    for label in ("providerRequest", "request"):
        body = d.get(label) or {}
        if body.get("_truncated"):
            print("   [%s] truncated %d KB" % (label, body.get("_originalSize", 0) // 1024))
            continue
        s = json.dumps(body)
        import re
        for pat in [r'"model"\s*:\s*"([^"]+)"', r"models/([A-Za-z0-9_.\-]+)",
                    r"upstreamModelId\"\s*:\s*\"([^\"]+)\""]:
            hits = sorted(set(re.findall(pat, s)))[:6]
            if hits:
                print("   [%s] %s -> %s" % (label, pat[:22], hits))
    lat = (d.get("latency") or {}).get("total", "?")
    print("   latency:", lat)
