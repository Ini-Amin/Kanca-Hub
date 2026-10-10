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
"""
from __future__ import annotations

import json
import time
from pathlib import Path

DEFAULT_LOG = Path("tests/fixtures/slider/attempts.jsonl")


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


if __name__ == "__main__":
    import sys
    print(stats(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LOG))