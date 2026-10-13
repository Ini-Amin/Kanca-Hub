#!/usr/bin/env python3
"""scripts/slider_review_merge.py — fold every label source into one table for the
review page.

Sources
  * /tmp/slider_review/manifest.json  — puzzle list: id, images, detector, flat_dark,
                                        known_truth, source
  * /tmp/slider_review/vision_all.json — {id: {x, conf}} from the vision subagents
                                        (ag/gemini-3.8-flash-high over the read tool)

A puzzle is "labeled" when it already has a trusted truth (team labels / real
passes) OR the diff detector and the vision model agree within TOL px. Everything
else is "verify" — the disagreements a human should adjudicate. The detector and
the vision model are independent oracles, so their agreement is decent evidence
and their disagreement is exactly the interesting case.

  python3 scripts/slider_review_merge.py [--tol 4]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SRC = Path("/tmp/slider_review")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tol", type=int, default=4, help="px agreement window (default 4)")
    a = ap.parse_args()

    puzzles = json.loads((SRC / "manifest.json").read_text())["puzzles"]
    vision = json.loads((SRC / "vision_all.json").read_text()) if (SRC / "vision_all.json").exists() else {}

    rows, n_lab, n_ver = [], 0, 0
    for p in puzzles:
        i = p["id"]
        det, truth = p.get("detector"), p.get("known_truth")
        v = vision.get(i) or {}
        vis, conf = v.get("x"), v.get("conf")
        if truth is not None or (vis is not None and det is not None and abs(vis - det) <= a.tol):
            status = "labeled"
            n_lab += 1
        else:
            status = "verify"
            n_ver += 1
        rows.append(dict(id=i, bg=p["bg"], piece=p["piece"], detector=det,
                         flat_dark=p.get("flat_dark"), vision=vis, conf=conf,
                         truth=truth, source=p.get("source") or "", status=status))

    # verify first, worst disagreement first
    rows.sort(key=lambda r: (r["status"] != "verify",
                             -abs((r["detector"] or 0) - (r["vision"] or 0))))
    (SRC / "merged.json").write_text(json.dumps(rows, indent=1))
    print(f"merged {len(rows)} puzzles -> {SRC/'merged.json'}  (labeled={n_lab} verify={n_ver})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
