#!/usr/bin/env python3
"""scripts/slider_review_merge.py — fold every label source into one table for the
review page. Everything lives in ONE folder now (reports/slider_review/).

Sources (all inside reports/slider_review/, override with SLIDER_DIR)
  * img/            — the puzzle images (bg + piece), keyed by <id>_bg.png
  * dataset.jsonl   — every attempt the solver ever made (pass AND fail)
  * labels.jsonl    — team labels (unanimous votes) = trusted truth
  * verified.json   — the human's own X from the review page = trusted truth
  * vision.json     — the vision-oracle X from the subagents (weak)
  * mined.json      — the RLHF pass-mined truth (a landed target = truth)
  * ws_verified.json — human labels for the Windscribe slider (SEPARATE table:
    reports/slider_review/ws.json holds the puzzle index, ws_img/ the images)

A puzzle is "labeled" when it already has a trusted truth (team / verified /
mined pass) OR the diff detector and the vision oracle agree within TOL px.
Everything else is "verify" — the disagreements a human should adjudicate.
Puzzles that only ever FAILED are surfaced as "failed" so their images land in
the page too (the user asked for the failed puzzles to be visible).

  python3 scripts/slider_review_merge.py [--tol 4]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DIR = Path(os.environ.get("SLIDER_DIR", REPO / "reports" / "slider_review"))


def _jsonl(path: Path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _json(path: Path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def _x_of(v):
    if isinstance(v, dict):
        return v.get("x")
    return v


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tol", type=int, default=4, help="px agreement window (default 4)")
    ap.add_argument("--dir", default=str(DIR))
    a = ap.parse_args()
    src = Path(a.dir)

    img = src / "img"
    truth: dict[str, int] = {}
    source: dict[str, str] = {}

    # trusted truth: team labels (strongest) > human labels > mined (pass)
    # Human labels come in two files: verified.json (early batch) and
    # slider_verified.json (the review page's export). Union them -- they may
    # not overlap, and the newer export must never be dropped.
    for row in _jsonl(src / "labels.jsonl"):
        bg = row.get("bg")
        if bg and row.get("label") is not None:
            i = bg.replace("_bg.png", "")
            truth[i] = int(row["label"])
            source[i] = row.get("source") or "team"
    for name in ("verified.json", "slider_verified.json"):
        for i, v in _json(src / name).items():
            x = _x_of(v)
            if x is not None:
                truth[i] = int(x)
                source[i] = "verified"
    for i, v in _json(src / "mined.json").items():
        x = _x_of(v)
        if x is not None and i not in truth:
            truth[i] = int(x)
            source[i] = "mined-pass"

    vision = _json(src / "vision.json")

    # puzzles seen in the dataset (with their detector guess + fail count)
    seen: dict[str, dict] = {}
    for r in _jsonl(src / "dataset.jsonl"):
        bg = (r.get("images") or {}).get("bg")
        if not bg:
            continue
        i = bg.replace("_bg.png", "")
        rec = seen.setdefault(i, {"attempts": 0, "fails": 0, "detector": None})
        rec["attempts"] += 1
        if not r.get("passed"):
            rec["fails"] += 1
        if rec["detector"] is None and r.get("detector_guess") is not None:
            rec["detector"] = int(r["detector_guess"])

    # the review page may carry a richer detector/flat_dark from the old run
    old = {p["id"]: p for p in _json_list(src / "merged_old.json")}

    # every image on disk is a puzzle candidate
    ids = sorted({f.name.replace("_bg.png", "") for f in img.glob("*_bg.png")})

    rows, n_lab, n_ver, n_fail = [], 0, 0, 0
    for i in ids:
        if not (img / f"{i}_piece.png").exists():
            continue
        o = old.get(i, {})
        s = seen.get(i, {})
        det = o.get("detector", s.get("detector"))
        fd = o.get("flat_dark")
        v = vision.get(i) or {}
        vis, conf = v.get("x"), v.get("conf")
        t = truth.get(i)
        if t is not None:
            status = "labeled"; n_lab += 1
        elif vis is not None and det is not None and abs(vis - det) <= a.tol:
            status = "labeled"; n_lab += 1
        elif s.get("fails", 0) and s.get("attempts", 0) == s.get("fails", 0):
            status = "failed"; n_fail += 1
        else:
            status = "verify"; n_ver += 1
        rows.append(dict(id=i, bg=f"{i}_bg.png", piece=f"{i}_piece.png",
                         detector=det, flat_dark=fd, vision=vis, conf=conf,
                         truth=t, source=source.get(i, o.get("source") or ""),
                         status=status, attempts=s.get("attempts", 0),
                         fails=s.get("fails", 0)))

    # Collapse pixel-identical puzzles. The Aliyun slider reuses the SAME
    # background image across sessions, and each session saves it under a fresh
    # random id, so the page was showing 921 rows for 577 distinct pictures --
    # 344 rows a human had to re-drag and re-label for no new information.
    # Dedupe by content hash, keep every truth value, and surface disagreements
    # wider than --tol as a conflict instead of silently picking one.
    import hashlib
    groups: dict[str, list[dict]] = {}
    for r in rows:
        f = img / r["bg"]
        try:
            h = hashlib.sha256(f.read_bytes()).hexdigest()
        except OSError:
            h = "missing:" + r["id"]
        groups.setdefault(h, []).append(r)

    deduped, n_dup, conflicts = [], 0, []
    for h, g in groups.items():
        g.sort(key=lambda r: (r["truth"] is None, r["status"] != "labeled"))
        keep = dict(g[0])
        keep["dupes"] = [r["id"] for r in g[1:]]
        n_dup += len(g) - 1
        truths = sorted({r["truth"] for r in g if r["truth"] is not None})
        if truths:
            # median resists a single mis-drag; verified/team labels already
            # outrank mined ones via the sort above.
            keep["truth"] = truths[len(truths) // 2]
            keep["source"] = next((r["source"] for r in g if r["truth"] == keep["truth"]), "")
        if len(truths) > 1 and truths[-1] - truths[0] > a.tol:
            conflicts.append({"id": keep["id"], "truths": truths,
                              "ids": [r["id"] for r in g]})
            keep["conflict"] = truths
        keep["attempts"] = sum(r["attempts"] for r in g)
        keep["fails"] = sum(r["fails"] for r in g)
        deduped.append(keep)
    rows = deduped

    # verify first (worst disagreement first), then failed, then labeled
    order = {"verify": 0, "failed": 1, "labeled": 2}
    rows.sort(key=lambda r: (order[r["status"]],
                             -abs((r["detector"] or 0) - (r["vision"] or 0))))
    (src / "merged.json").write_text(json.dumps(rows, indent=1))
    # Recount AFTER dedupe: the n_lab/n_ver/n_fail tallies above counted rows
    # that no longer exist, so the printed totals disagreed with the file.
    n_lab = sum(1 for r in rows if r["status"] == "labeled")
    n_ver = sum(1 for r in rows if r["status"] == "verify")
    n_fail = sum(1 for r in rows if r["status"] == "failed")
    print(f"merged {len(rows)} puzzles -> {src/'merged.json'}  "
          f"(labeled={n_lab} verify={n_ver} failed={n_fail}, "
          f"collapsed {n_dup} duplicate image rows, {len(conflicts)} real conflicts)")
    if conflicts:
        (src / "conflicts.json").write_text(json.dumps(conflicts, indent=1))
        print(f"  -> {src/'conflicts.json'} (same picture, labels differ by more than {a.tol}px)")

    ws_meta = _json(src / "ws.json")
    ws_truth = {i: (int(_x_of(v)), v.get("top") if isinstance(v, dict) else None)
                for i, v in _json(src / "ws_verified.json").items()
                if _x_of(v) is not None}
    ws_rows = []
    for i, m in sorted(ws_meta.items()):
        if not (src / "ws_img" / f"{i}_bg.png").exists():
            continue
        t, t_top = ws_truth.get(i, (None, None))
        ws_rows.append(dict(id=i, bg=f"{i}_bg.png", piece=f"{i}_piece.png",
                            detector=None, flat_dark=None, vision=None, conf=None,
                            truth=t, source="verified" if t is not None else "",
                            status="labeled" if t is not None else "windscribe",
                            attempts=0, fails=0, top=t_top if t_top is not None else m.get("top")))
    (src / "ws_merged.json").write_text(json.dumps(ws_rows, indent=1))
    n_ws_lab = sum(1 for r in ws_rows if r["truth"] is not None)
    print(f"windscribe {len(ws_rows)} puzzles -> {src/'ws_merged.json'}  "
          f"(labeled={n_ws_lab})")
    return 0


def _json_list(path: Path):
    v = _json(path)
    return v if isinstance(v, list) else []


if __name__ == "__main__":
    raise SystemExit(main())
