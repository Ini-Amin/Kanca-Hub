"""Refactor safety net: pin kancahub's PUBLIC COMMAND SURFACE.

Every top-level command and subcommand (and the set of argument destinations
each accepts) is captured in tests/kancahub_surface.json. If a refactor drops,
renames, or re-nests a command, this fails loudly — so we can split the
4,677-line kancahub.py without silently losing a feature.

To intentionally change the surface, regenerate the fixture:
  python -c "import sys;sys.path.insert(0,'scripts');import kancahub,json,argparse; \
    p=kancahub.build_parser(); ..."   (or run scripts/gen_cli_surface.py)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
import unittest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
FIXTURE = Path(__file__).resolve().parent / "kancahub_surface.json"


def _walk(parser, path=""):
    out = {}
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            for name, sp in a.choices.items():
                key = (path + " " + name).strip()
                out[key] = sorted({act.dest for act in sp._actions})
                out.update(_walk(sp, key))
    return out


def _live_surface() -> dict:
    import kancahub
    return _walk(kancahub.build_parser())


class TestCommandSurface(unittest.TestCase):
    def setUp(self):
        raw = FIXTURE.read_text().strip()
        self.assertTrue(raw, f"fixture {FIXTURE.name} is empty — run scripts/gen_cli_surface.py")
        self.expected = json.loads(raw)
        self.live = _live_surface()

    def test_no_command_dropped(self):
        missing = sorted(set(self.expected) - set(self.live))
        self.assertEqual(missing, [], f"commands missing after change: {missing}")

    def test_no_command_unexpectedly_added(self):
        # new commands are allowed, but flag them so the fixture gets refreshed
        added = sorted(set(self.live) - set(self.expected))
        self.assertEqual(added, [], f"new commands (refresh the fixture): {added}")

    def test_no_argument_dest_lost(self):
        gone = {k: sorted(set(self.expected[k]) - set(self.live.get(k, [])))
                for k in self.expected if set(self.expected[k]) - set(self.live.get(k, []))}
        self.assertEqual(gone, {}, f"argument destinations lost: {gone}")


if __name__ == "__main__":
    unittest.main()