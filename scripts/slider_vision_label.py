#!/usr/bin/env python3
"""
scripts/slider_vision_label.py — label Aliyun slider puzzles by VISION (offline).

Sends each (background, piece) pair to a 9Router vision model and asks for the x
of the missing region. Used OFFLINE to grow the dataset and to fit a tiny
torch-free detector — the vision model is NOT part of the shipped CLI.

Usage:
  camoufox-venv/bin/python scripts/slider_vision_label.py --dir tests/fixtures/slider --score
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
import captcha_slider as CS  # noqa: E402

MODELS = ["cbai/glm-5v-turbo", "THK/gemini-3.8-flash", "ag/gemini-3.8-flash-low"]

ASK = (
    "Image 1 is a picture that has ONE object removed (a region is missing/smeared). "
    "Image 2 is the missing object. Reply with ONLY the integer x pixel (0-300) of the "
    "LEFT edge of the missing region in Image 1. Number only, no words."
)


def _b64(p: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()


def ask_vision(bg: Path, pc: Path, model: str, key: str, base="http://localhost:20128",
               timeout: float = 90) -> int | None:
    body = {"model": model, "stream": False, "max_tokens": 24,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": ASK},
                {"type": "image_url", "image_url": {"url": _b64(bg)}},
                {"type": "image_url", "image_url": {"url": _b64(pc)}}]}]}
    req = urllib.request.Request(f"{base}/v1/chat/completions", data=json.dumps(body).encode(),
                                 method="POST",
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"})
    try:
        raw = urllib.request.urlopen(req, timeout=timeout).read().decode()
    except Exception:  # noqa: BLE001
        return None
    s = ""
    if raw.lstrip().startswith("data:"):
        for ln in raw.splitlines():
            ln = ln.strip()
            if ln.startswith("data:") and ln[5:].strip() not in ("", "[DONE]"):
                try:
                    for ch in json.loads(ln[5:].strip()).get("choices", []):
                        s += ((ch.get("delta") or {}).get("content")) or ""
                except Exception:  # noqa: BLE001
                    pass
    else:
        try:
            s = (json.loads(raw).get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
        except Exception:  # noqa: BLE001
            return None
    m = re.search(r"\d+", s)
    return int(m.group(0)) if m else None


def poly(d: float) -> float:
    a, b, c = CS.COEF
    return a * d * d + b * d + c


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Vision-label slider puzzles")
    ap.add_argument("--dir", default="tests/fixtures/slider")
    ap.add_argument("--score", action="store_true", help="compare to truth (requires drag_left_px)")
    ap.add_argument("--model", default=None)
    a = ap.parse_args(argv)
    D = Path(a.dir)
    labels = json.loads((D / "labels_all.json").read_text()) if (D / "labels_all.json").exists() \
        else json.loads((D / "labels.json").read_text())
    key = CS._r9_key()
    models = [a.model] if a.model else MODELS
    rows = []
    for L in labels:
        bg, pc = D / L["bg"], D / L["pc"]
        if not bg.exists() or not pc.exists():
            continue
        x = None
        for m in models:
            x = ask_vision(bg, pc, m, key)
            if x is not None:
                break
        truth = poly(int(L["drag_left_px"].replace("px", ""))) if L.get("drag_left_px") else L.get("gap_x")
        rows.append((L.get("puzzle", "?"), truth, x))
        flag = ""
        if truth is not None and x is not None:
            flag = "OK" if abs(x - truth) <= 12 else f"err{abs(x-truth):.0f}"
        print(f"  p{L.get('puzzle','?'):>3}: truth={truth if truth is None else round(truth)} vision={x} {flag}", flush=True)
    if a.score:
        sc = [r for r in rows if r[1] is not None and r[2] is not None]
        ok = sum(1 for _, t, x in sc if abs(x - t) <= 12)
        print(f"\nwithin12 = {ok}/{len(sc)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
