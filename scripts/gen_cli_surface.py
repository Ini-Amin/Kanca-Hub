#!/usr/bin/env python3
"""Regenerate tests/kancahub_surface.json from the live kancahub parser.

Run this ONLY when you intentionally add/rename a command (the surface test in
tests/test_cli_surface.py fails on purpose to force a review). Must import
kancahub the normal way so sys.modules['kancahub'] exists (kancahub.py sets it).
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
import kancahub  # noqa: E402


def walk(parser, path=""):
    out = {}
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            for name, sp in a.choices.items():
                key = (path + " " + name).strip()
                out[key] = sorted({act.dest for act in sp._actions})
                out.update(walk(sp, key))
    return out


def main() -> int:
    data = walk(kancahub.build_parser())
    out = REPO / "tests" / "kancahub_surface.json"
    out.write_text(json.dumps(data, indent=0, sort_keys=True))
    print(f"  wrote {len(data)} commands -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
