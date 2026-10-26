#!/usr/bin/env python3
"""extract_sourcemap.py — Extracts original unminified source files from Webpack source maps.

Usage:
  python3 scripts/extract_sourcemap.py /tmp/ext_tuxler/ artifacts/re_tuxler/src/
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def extract_from_map(map_path: Path, output_base_dir: Path) -> int:
    try:
        with open(map_path, "r", encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[-] Failed reading {map_path}: {e}")
        return 0

    sources = data.get("sources", [])
    contents = data.get("sourcesContent", [])

    if not sources or not contents or len(sources) != len(contents):
        print(f"[-] Invalid or empty source map in {map_path}")
        return 0

    count = 0
    for src_rel, content in zip(sources, contents):
        if not content:
            continue

        # Clean webpack prefix
        cleaned = src_rel
        for prefix in ("webpack:///", "webpack://", "./"):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix) :]

        target_file = output_base_dir / cleaned
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(content, encoding="utf-8")
        count += 1

    print(f"[+] Extracted {count} original source files from {map_path.name}")
    return count


def main():
    parser = argparse.ArgumentParser(description="Webpack SourceMap Extractor")
    parser.add_argument("source_dir", help="Directory containing .map files")
    parser.add_argument("output_dir", help="Directory to save extracted source trees")
    args = parser.parse_args()

    src_dir = Path(args.source_dir).resolve()
    out_dir = Path(args.output_dir).resolve()

    if not src_dir.is_dir():
        sys.exit(f"Source directory not found: {src_dir}")

    map_files = list(src_dir.rglob("*.map"))
    print(f"[*] Found {len(map_files)} sourcemap file(s) in {src_dir}")

    total_extracted = 0
    for mf in map_files:
        total_extracted += extract_from_map(mf, out_dir)

    print(f"\n========================================================")
    print(f"[SUCCESS] Reconstructed {total_extracted} original sources!")
    print(f"Location: {out_dir}")
    print(f"========================================================")


if __name__ == "__main__":
    main()
