#!/usr/bin/env python3
"""Vision solver for hCaptcha puzzles, driven by the captured dataset.

WHAT IT DOES
------------
Takes a captured puzzle (the canvas screenshot + the prompt) and asks a vision
model which points to click / where to drag. Two modes:

    solve  --id <puzzle-id>       answer one stored puzzle
    solve  --latest               answer the newest stored puzzle
    bench                         score the solver against accepted labels

The answer is a list of click points in CANVAS coordinates plus, for drag
puzzles, a from/to pair. That is exactly what the capture records, so a correct
answer can be replayed into the browser.

WHY THE PROMPT LOOKS LIKE THIS
------------------------------
hCaptcha prompts are short imperatives with a target property ("things that are
easy to tear by hand", "the animal that lays eggs", "the vial matching its
shape"). The hard part is not vision, it is grounding the property in the image.
So the prompt forces the model to (1) restate the property, (2) enumerate the
visible candidates, (3) pick, and only then (4) emit coordinates -- which makes
a wrong answer diagnosable instead of a bare number.

Every call also receives any ACCEPTED examples for the same prompt text from
labels.jsonl (few-shot). The dataset therefore improves the solver directly,
which is the whole point of capturing it.

MODEL
-----
Default: `vs/glm-4.6v-flash-free` (free, vision-capable, served by the local
9router). Override with HCAPTCHA_VISION_MODEL. The 9router key is read from
~/.9router or the environment, same as captcha_slider.py.

USAGE
    python3 scripts/hcaptcha_solver.py bench
    python3 scripts/hcaptcha_solver.py solve --latest
    python3 scripts/hcaptcha_solver.py solve --id cde674e2199c3ee9 --json
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DIR = Path(os.environ.get("HCAPTCHA_DIR", REPO / "reports" / "hcaptcha_review"))
DATASET = DIR / "dataset.jsonl"
LABELS = DIR / "labels.jsonl"
IMG = DIR / "img"

DEFAULT_MODEL = os.environ.get("HCAPTCHA_VISION_MODEL", "ag/gemini-3.8-flash-high")
ROUTER = os.environ.get("HCAPTCHA_ROUTER", "http://127.0.0.1:20128")
KEY_FILE = Path.home() / ".9router" / "router.key"

PROMPT = """You are solving an hCaptcha puzzle.

TASK TEXT: {question}

The image is the puzzle canvas, {w}x{h} pixels. Coordinates are in that canvas
space, origin top-left.

Work in this order and show your reasoning briefly:
1. Restate what property the task asks for, in one short sentence.
2. List the distinct candidate objects you can see, with an approximate centre
   (x, y) for each.
3. Decide which candidates satisfy the property.
4. Emit the answer.

For a click/tile task, give every tile that should be clicked.
For a drag task ("drag X to Y", "move X into the slot"), give the source and the
destination.

End with a single JSON object on its own line, nothing after it:
{{"action": "click", "points": [{{"x": 123, "y": 456}}], "reason": "..."}}
or
{{"action": "drag", "from": {{"x": 100, "y": 200}}, "to": {{"x": 300, "y": 260}}, "reason": "..."}}
"""


def _router_key() -> str:
    for env in ("HCAPTCHA_ROUTER_KEY", "ROUTER_KEY", "NINE_ROUTER_KEY"):
        v = os.environ.get(env)
        if v:
            return v.strip()
    if KEY_FILE.is_file():
        return KEY_FILE.read_text().strip()
    # fall back to the key used elsewhere in this repo
    cfg = Path.home() / ".config" / "opencode" / "opencode.json"
    try:
        d = json.loads(cfg.read_text())
        return ((d.get("provider") or {}).get("9router") or {}).get("options", {}).get("apiKey", "")
    except Exception:
        return ""


def _b64(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def _read_jsonl(p: Path) -> list[dict]:
    if not p.is_file():
        return []
    out = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _ask(model: str, question: str, image_path: Path, w: int, h: int,
         examples: list[dict], timeout: int = 120) -> str:
    key = _router_key()
    if not key:
        raise RuntimeError("no 9router key found (~/.9router/router.key or opencode.json)")

    content = [{"type": "text", "text": PROMPT.format(question=question, w=w, h=h)}]

    # Few-shot: accepted answers for the same task text. Images of past puzzles
    # are deliberately NOT included (they would bias toward the old layout);
    # only the decided points are, as a shape hint.
    if examples:
        ex_txt = "\n".join(
            f'- "{e.get("question", "")[:80]}" -> {json.dumps(e.get("clicks") or [])}'
            for e in examples[:5]
        )
        content.append({"type": "text",
                        "text": "Previously accepted answers for similar tasks "
                                "(coordinates are examples of the expected shape):\n" + ex_txt})

    content.append({"type": "image_url", "image_url": {"url": _b64(image_path)}})

    body = {
        "model": model,
        "stream": False,
        "max_tokens": 900,
        "messages": [{"role": "user", "content": content}],
    }
    req = urllib.request.Request(
        f"{ROUTER.rstrip('/')}/v1/chat/completions",
        data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode()

    # 9router sometimes answers as an SSE stream even for stream=False.
    if raw.lstrip().startswith("data:"):
        out = ""
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                j = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            for ch in j.get("choices") or []:
                out += ((ch.get("delta") or {}).get("content")) or ""
        return out
    j = json.loads(raw)
    return ((j.get("choices") or [{}])[0].get("message") or {}).get("content", "") or ""


def parse_answer(text: str) -> dict | None:
    """Pull the last JSON object out of the model answer."""
    best = None
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start >= 0:
                try:
                    obj = json.loads(text[start:i + 1])
                except Exception:
                    continue
                if "action" in obj or "points" in obj:
                    best = obj
                start = -1
    return best


def solve_one(row: dict, model: str) -> dict:
    png = row.get("challenge_png")
    if not png or not (IMG / png).is_file():
        return {"ok": False, "error": "no image for this puzzle"}
    canvas = row.get("canvas") or {}
    w, h = canvas.get("w") or 500, canvas.get("h") or 470

    labels = _read_jsonl(LABELS)
    same = [l for l in labels if l.get("question", "").strip() == (row.get("question") or "").strip()]

    text = _ask(model, row.get("question") or "", IMG / png, w, h, same)
    ans = parse_answer(text)
    return {
        "ok": ans is not None,
        "id": row.get("id"),
        "question": row.get("question"),
        "canvas": canvas,
        "answer": ans,
        "raw": text[-1200:],
        "examples_used": len(same[:5]),
    }


def _iou_points(pred: list[dict], truth: list[dict], tol: int = 60) -> float:
    """Fraction of predicted points that land near an accepted point."""
    if not pred:
        return 0.0
    if not truth:
        return 0.0
    hit = 0
    for p in pred:
        if any(abs(p.get("x", 0) - t.get("x", 0)) <= tol and
               abs(p.get("y", 0) - t.get("y", 0)) <= tol for t in truth):
            hit += 1
    return hit / len(pred)


def cmd_solve(a) -> int:
    rows = _read_jsonl(DATASET)
    if not rows:
        print("no dataset rows yet; run: hcaptcha_dataset.py capture")
        return 1
    if a.id:
        row = next((r for r in rows if r.get("id") == a.id), None)
        if not row:
            print(f"no puzzle with id {a.id}")
            return 1
    else:
        row = rows[-1]

    res = solve_one(row, a.model)
    if a.json:
        print(json.dumps(res, indent=1))
    else:
        print(f"puzzle  : {res['id']}")
        print(f"question: {res['question']}")
        print(f"canvas  : {res['canvas']}")
        print(f"answer  : {json.dumps(res['answer'])}")
        print(f"few-shot: {res['examples_used']} accepted example(s)")
    return 0 if res["ok"] else 2


def cmd_bench(a) -> int:
    labels = {l["id"]: l for l in _read_jsonl(LABELS)}
    rows = [r for r in _read_jsonl(DATASET) if r.get("id") in labels]
    if not rows:
        print("no accepted labels to bench against yet.")
        print("Solve some puzzles with `hcaptcha_dataset.py capture` first; "
              "each PASS becomes a label.")
        return 1
    scores = []
    for row in rows[-a.limit:]:
        res = solve_one(row, a.model)
        if not res["ok"]:
            scores.append(0.0)
            continue
        ans = res["answer"] or {}
        pred = ans.get("points") or ([ans["from"], ans["to"]] if ans.get("from") else [])
        truth = labels[row["id"]].get("clicks") or []
        scores.append(_iou_points(pred, truth))
    if not scores:
        print("nothing scored")
        return 1
    print(f"model   : {a.model}")
    print(f"puzzles : {len(scores)}")
    print(f"mean hit: {sum(scores) / len(scores):.0%}")
    print(f"perfect : {sum(1 for s in scores if s >= 0.99)}/{len(scores)}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("solve", help="answer one captured puzzle")
    s.add_argument("--id", default=None)
    s.add_argument("--latest", action="store_true")
    s.add_argument("--model", default=DEFAULT_MODEL)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_solve)

    s = sub.add_parser("bench", help="score the solver against accepted labels")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--model", default=DEFAULT_MODEL)
    s.set_defaults(fn=cmd_bench)

    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
