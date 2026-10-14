#!/usr/bin/env python3
"""
scripts/slider_attempts.py — store EVERY slider attempt (pass AND fail) + evaluate.

Pipeline (the reuse loop):
  fetch image -> guess gap -> drag -> result(pass/fail)
  -> STORE {bg, piece, guess, result}      (whatever the outcome)
  -> PASS: guess == true gap   (positive label)
     FAIL: true gap != guess   (negative evidence)

This module just logs and evaluates; the browser side lives in the collector/solver.
  - append(bg_path, pc_path, guess, passed)     -> writes a row to attempts.jsonl
  - evaluate(bg_path, pc_path, guess)           -> what the stored evidence says
      ('pass' if an identical puzzle+guess passed before, 'fail' if it failed,
       'unknown' otherwise). Reused as a reference so we never retry a known-bad
       gap on a known puzzle, and can pre-load known-good gaps.

RLHF-style closed loop (no human in the loop):
  A passing drag is a free LABEL: the target X that landed IS the truth for that
  puzzle image. `mine_labels()` turns every pass into (image -> X) ground truth,
  `curve()` refits the pixel<->drag quadratic from mid-drag probes, and
  `report()` scores every oracle (detector / flat_dark / vision) against the
  mined labels so the loop can see whether it is improving. `harvest_cmd()` is
  the entry point a timer/cron calls to keep growing the dataset on its own.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

DEFAULT_LOG = Path("tests/fixtures/slider/attempts.jsonl")
# The live dataset the solver/collector writes to (see captcha_slider.DATA_LOG).
LIVE_LOG = Path(os.environ.get(
    "SLIDER_DATASET", Path.home() / ".zcode-bridge" / "slider_dataset.jsonl"))
# Mined ground truth (image id -> X) that the loop accumulates from passes.
MINE_PATH = Path(os.environ.get(
    "SLIDER_MINED", Path.home() / ".zcode-bridge" / "slider_mined.json"))
# Human/verified labels (the review page export) are the strongest truth.
VERIFIED_PATH = Path(os.environ.get(
    "SLIDER_VERIFIED", Path.home() / ".zcode-bridge" / "slider_verified.json"))


def _hash(bg_path: Path, pc_path: Path) -> str:
    """A stable id for the puzzle from the two images (content hash)."""
    import hashlib
    h = hashlib.sha256()
    for p in (bg_path, pc_path):
        try:
            h.update(p.read_bytes())
        except Exception:  # noqa: BLE001
            h.update(str(p).encode())
    return h.hexdigest()[:16]


def append(bg_path, pc_path, guess: float, passed: bool, log_path=DEFAULT_LOG) -> dict:
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": int(time.time()), "puzzle": _hash(Path(bg_path), Path(pc_path)),
           "bg": str(bg_path), "pc": str(pc_path),
           "guess": int(guess), "result": "pass" if passed else "fail"}
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def load(log_path=DEFAULT_LOG) -> list[dict]:
    log_path = Path(log_path)
    if not log_path.exists():
        return []
    out = []
    for line in log_path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:  # noqa: BLE001
                pass
    return out


def evaluate(bg_path, pc_path, guess: float, log_path=DEFAULT_LOG) -> tuple[str, list[int]]:
    """Return (verdict, known_bad_x) for this puzzle + guess, from stored evidence.

    verdict: 'pass'  the same puzzle+guess passed before (reuse it!)
             'fail'  the same puzzle+guess failed before (don't retry)
             'unknown'
    known_bad_x: every x that already FAILED on this puzzle (avoid these).
    """
    pid = _hash(Path(bg_path), Path(pc_path))
    rows = [r for r in load(log_path) if r.get("puzzle") == pid]
    bad = sorted({r["guess"] for r in rows if r.get("result") == "fail"})
    hits = [r for r in rows if r.get("guess") == int(guess)]
    if hits:
        return ("pass" if hits[-1]["result"] == "pass" else "fail"), bad
    # if a pass exists for this puzzle, reuse its guess
    passes = [r["guess"] for r in rows if r.get("result") == "pass"]
    if passes:
        return f"known_pass:{passes[-1]}", bad
    return "unknown", bad


def stats(log_path=DEFAULT_LOG) -> str:
    rows = load(log_path)
    n = len(rows)
    p = sum(1 for r in rows if r.get("result") == "pass")
    puzzles = len({r.get("puzzle") for r in rows})
    return f"attempts={n} pass={p} fail={n-p} puzzles={puzzles}"


# ─────────────────────────────────────────────────────────── RLHF closed loop
STORE_PATH = Path(os.environ.get(
    "SLIDER_LABEL_STORE", Path.home() / ".zcode-bridge" / "slider_labels_store.json"))


def mine_labels(dataset=LIVE_LOG, out=MINE_PATH, store=STORE_PATH,
                data_dir=None) -> dict:
    """Every PASSED drag is a free label: the target that landed = the truth X for
    that image. Merge the human-verified labels (strongest) over the mined ones
    and write {image_id: X}. Returns the merged map.

    Image id = the `images.bg` basename without `_bg.png`, which is what the
    review page and the dataset both key on.

    Also writes a CONTENT-HASH store (slider_labels_store.json) keyed by the
    sha256 of the bg image bytes, so a re-rendered puzzle with a new puzzle_id
    still hits (captcha_slider.gap_by_label reads this).
    """
    import hashlib
    truth: dict[str, int] = {}
    by_hash: dict[str, dict] = {}
    data_dir = Path(data_dir) if data_dir is not None else Path(os.environ.get(
        "SLIDER_DATA_DIR", Path.home() / ".zcode-bridge" / "slider_data"))

    def _record(iid: str, x: int, source: str) -> None:
        truth[iid] = int(x)
        fp = data_dir / f"{iid}_bg.png"
        if fp.exists():
            try:
                h = hashlib.sha256(fp.read_bytes()).hexdigest()[:16]
                by_hash[h] = {"x": int(x), "source": source, "id": iid}
            except OSError:
                pass

    # 1. mined: a pass means target X is correct for that puzzle image
    for r in _iter_jsonl(dataset):
        if not r.get("passed"):
            continue
        tgt = r.get("target")
        bg = (r.get("images") or {}).get("bg")
        if tgt is None or not bg:
            continue
        _record(bg.replace("_bg.png", ""), int(tgt), "mined")
    # 2. human labels win (they were judged by eye, not by a pass). Files:
    #    verified.json (early batch) + slider_verified.json (review export) +
    #    ws_verified.json (windscribe section export) +
    #    slider_verified_windscribe.json (alt export name — same shape).
    for name in ("slider_verified.json", "verified.json",
                 "ws_verified.json", "slider_verified_windscribe.json"):
        for iid, v in _load_json(VERIFIED_PATH.parent / name).items():
            x = v.get("x") if isinstance(v, dict) else v
            if x is not None:
                _record(iid, int(x), "verified")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(truth, indent=1))
    Path(store).write_text(json.dumps(by_hash, indent=1))
    return truth


def curve(dataset=LIVE_LOG) -> dict:
    """Refit the piece-vs-slider quadratic from every mid-drag probe and compare
    with the shipped COEF. Returns {"coef": (a,b,c), "rms": px, "n": samples}."""
    pts = []
    for r in _iter_jsonl(dataset):
        for s, p in (r.get("probes") or []):
            if s and s > 0:
                pts.append((float(s), float(p)))
    if len(pts) < 6:
        return {"coef": None, "rms": None, "n": len(pts)}
    import statistics
    best = None
    for a10 in range(0, 60):          # grid-search the quadratic term a
        a = a10 / 10000.0
        xs = [s for s, _ in pts]
        ys = [p - a * s * s for s, p in pts]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        sxx = sum((x - mx) ** 2 for x in xs)
        if sxx == 0:
            continue
        b1 = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        c1 = my - b1 * mx
        err = sum((p - (a * s * s + b1 * s + c1)) ** 2 for s, p in pts) / len(pts)
        if best is None or err < best[0]:
            best = (err, a, b1, c1)
    if not best:
        return {"coef": None, "rms": None, "n": len(pts)}
    err, a, b1, c1 = best
    return {"coef": (a, b1, c1), "rms": err ** 0.5, "n": len(pts)}


def report(dataset=LIVE_LOG, tol: int = 5) -> str:
    """Score every oracle against the mined+verified truth so the loop can see
    whether it is improving. This is the RLHF reward signal (no human needed)."""
    import base64
    try:
        import captcha_slider as CS
    except Exception:  # noqa: BLE001
        return "report: captcha_slider not importable"
    truth = mine_labels(dataset)
    data_dir = Path(os.environ.get(
        "SLIDER_DATA_DIR", Path.home() / ".zcode-bridge" / "slider_data"))
    rows = []
    for iid, x in sorted(truth.items()):
        bg, pc = data_dir / f"{iid}_bg.png", data_dir / f"{iid}_piece.png"
        if not (bg.exists() and pc.exists()):
            continue
        b = base64.b64encode(bg.read_bytes()).decode()
        p = base64.b64encode(pc.read_bytes()).decode()
        rows.append((x, CS.gap_by_diff(b, p), CS.flat_dark_guess(b, p)))
    if not rows:
        return "report: no labeled images on disk"
    lines = [f"labeled puzzles: {len(rows)}  (tol={tol}px)"]
    for idx, name in ((1, "detector"), (2, "flat_dark")):
        errs = [abs(r[idx] - r[0]) for r in rows if r[idx] is not None]
        if not errs:
            continue
        hit = sum(1 for e in errs if e <= tol)
        lines.append("  %-10s n=%d hit@%dpx=%d (%.0f%%) mean_err=%.1f"
                     % (name, len(errs), tol, hit, 100.0 * hit / len(errs),
                        sum(errs) / len(errs)))
    # pass rate on the raw dataset (per-drag and per-unique-puzzle)
    recs = list(_iter_jsonl(dataset))
    n = len(recs)
    p = sum(1 for r in recs if r.get("passed"))
    uniq = len({r.get("puzzle") for r in recs})
    passed_uniq = len({r.get("puzzle") for r in recs if r.get("passed")})
    lines.append("  per-drag pass: %d/%d (%.1f%%)   per-puzzle pass: %d/%d (%.1f%%)"
                 % (p, n, 100.0 * p / max(1, n), passed_uniq, uniq,
                    100.0 * passed_uniq / max(1, uniq)))
    c = curve(dataset)
    if c["coef"]:
        lines.append("  fitted COEF=(%.8f, %.8f, %.8f) rms=%.2fpx (n=%d)"
                     % (c["coef"][0], c["coef"][1], c["coef"][2], c["rms"], c["n"]))
    return "\n".join(lines)


def cv(dataset=LIVE_LOG, folds: int = 5, tol: int = 5) -> str:
    """Supervised cross-validation by content hash: train/test split so
    detector accuracy is measured strictly on UNSEEN puzzles."""
    import base64
    import hashlib
    try:
        import captcha_slider as CS
    except Exception:  # noqa: BLE001
        return "cv: captcha_slider not importable"
    truth = mine_labels(dataset)
    data_dir = Path(os.environ.get(
        "SLIDER_DATA_DIR", Path.home() / ".zcode-bridge" / "slider_data"))
    items = []
    for iid, x in sorted(truth.items()):
        bg, pc = data_dir / f"{iid}_bg.png", data_dir / f"{iid}_piece.png"
        if not (bg.exists() and pc.exists()):
            continue
        try:
            h = hashlib.sha256(bg.read_bytes()).hexdigest()[:16]
        except OSError:
            continue
        items.append({"id": iid, "hash": h, "bg": bg, "pc": pc, "x": x})
    if not items:
        return "cv: no labeled images on disk"
    hashes = sorted({it["hash"] for it in items})
    k = max(2, min(folds, len(hashes)))
    hash_to_fold = {h: i % k for i, h in enumerate(hashes)}
    lines = [f"cross-validation ({k} folds by content hash, n={len(items)} items, {len(hashes)} unique puzzles, tol={tol}px):"]
    all_errs: dict[str, list[int]] = {"detector": [], "flat_dark": []}
    for fold_idx in range(k):
        test_items = [it for it in items if hash_to_fold[it["hash"]] == fold_idx]
        fold_errs: dict[str, list[int]] = {"detector": [], "flat_dark": []}
        for it in test_items:
            b = base64.b64encode(it["bg"].read_bytes()).decode()
            p = base64.b64encode(it["pc"].read_bytes()).decode()
            d = CS.gap_by_diff(b, p)
            fd = CS.flat_dark_guess(b, p)
            if d is not None:
                fold_errs["detector"].append(abs(d - it["x"]))
            if fd is not None:
                fold_errs["flat_dark"].append(abs(fd - it["x"]))
        for name in ("detector", "flat_dark"):
            errs = fold_errs[name]
            all_errs[name].extend(errs)
            hit = sum(1 for e in errs if e <= tol)
            n = len(errs)
            pct = 100.0 * hit / max(1, n)
            mean_e = sum(errs) / max(1, n)
            lines.append("  fold %d %-10s n=%d hit@%dpx=%d (%.0f%%) mean_err=%.1f"
                         % (fold_idx, name, n, tol, hit, pct, mean_e))
    lines.append("  -- aggregate held-out evaluation --")
    for name in ("detector", "flat_dark"):
        errs = all_errs[name]
        hit = sum(1 for e in errs if e <= tol)
        n = len(errs)
        pct = 100.0 * hit / max(1, n)
        mean_e = sum(errs) / max(1, n)
        lines.append("  %-10s n=%d hit@%dpx=%d (%.0f%%) mean_err=%.1f"
                         % (name, n, tol, hit, pct, mean_e))
    return "\n".join(lines)


def harvest_cmd(count: int = 15, headless: bool = False) -> int:
    """Keep growing the dataset on its own: solve N fresh puzzles via the
    collector, which appends to the dataset (each pass = a new free label).
    This is what a systemd timer / cron should call."""
    import subprocess
    import sys
    here = Path(__file__).resolve().parent
    cmd = [sys.executable, str(here / "captcha_slider.py"), "harvest",
           "--count", str(count)]
    if headless:
        cmd.append("--headless")
    return subprocess.call(cmd)


def tick(harvest: int = 0, rebuild_page: bool = True) -> str:
    """ONE autonomous pass of the whole loop — the thing a timer calls:

      1. mine  : passes -> labels_store.json (free labels / reward signal)
      2. curve : refit the pixel<->drag quadratic from fresh probes
      3. report: score the oracles vs the labels (so drift is visible)
      4. page  : rebuild the review page so the human can label the hard ones
      5. harvest(optional): solve N fresh puzzles to keep the data flowing

    No human needed; returns the report text so a timer can log it.
    """
    import subprocess
    import sys
    here = Path(__file__).resolve().parent
    if harvest > 0:
        try:
            harvest_cmd(harvest)
        except Exception:  # noqa: BLE001
            pass
    mine_labels()
    if rebuild_page:
        for script in ("slider_review_merge.py", "slider_review_html.py"):
            try:
                subprocess.run([sys.executable, str(here / script)],
                               capture_output=True, timeout=300)
            except Exception:  # noqa: BLE001
                pass
    return report()


# ─────────────────────────────────────────────────────────── small helpers
def _iter_jsonl(path):
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                yield json.loads(line)
            except Exception:  # noqa: BLE001
                continue


def _load_json(path):
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return {}


if __name__ == "__main__":
    import sys
    sub = sys.argv[1] if len(sys.argv) > 1 else "stats"
    if sub == "mine":
        t = mine_labels()
        print(f"mined {len(t)} labels -> {MINE_PATH}")
    elif sub == "report":
        print(report())
    elif sub == "cv":
        k = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 5
        print(cv(folds=k))
    elif sub == "tick":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        print(tick(harvest=n))
    elif sub == "harvest":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 15
        raise SystemExit(harvest_cmd(n))
    else:
        print(stats(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LOG))
