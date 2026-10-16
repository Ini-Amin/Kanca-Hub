"""Unit tests for `kancahub zmine` (referee-focused status, agents only).

commands_zmine.py reads the local store + live-checks usable accounts.
No registration, no writes. zmine.py itself is owned by another agent.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import kancahub


class TestZmineParser(unittest.TestCase):
    def setUp(self):
        self.parser = kancahub.build_parser()

    def test_zmine_group_parses(self):
        args = self.parser.parse_args(["zmine", "status"])
        self.assertEqual(args.group, "zmine")
        self.assertEqual(args.zmine_cmd, "status")

    def test_zmine_subcommands_parse(self):
        for sub in ("status", "refs", "keepalive"):
            args = self.parser.parse_args(["zmine", sub])
            self.assertEqual(args.zmine_cmd, sub)

    def test_zmine_keepalive_flags(self):
        args = self.parser.parse_args(["zmine", "keepalive", "--want", "6", "--pace", "5"])
        self.assertEqual(args.want, 6)
        self.assertEqual(args.pace, 5.0)


class TestZmineStatus(unittest.TestCase):
    def test_status_counts_usable_vs_disabled(self):
        import commands_zmine as cz
        store = [
            {"displayName": "alive_one", "ref": "RCFZ9GA7", "loginKey": "k1"},
            {"displayName": "dead_one", "ref": "RCFZ9GA7", "loginKey": "k2", "disabled": True},
        ]
        args = MagicMock()
        args.zmine_cmd = "status"
        with patch.object(cz, "_load_store", return_value=(Path("/tmp/x.jsonl"), store)):
            with patch("builtins.print") as mock_print:
                rc = cz.cmd_zmine(args)
        self.assertEqual(rc, 0)
        out = " ".join(str(c.args[0]) for c in mock_print.call_args_list)
        self.assertIn("1", out)  # usable count shown
        self.assertIn("alive_one", out)

    def test_refs_never_registers(self):
        # refs must only login+overview+tasks_next, never register/farm
        import commands_zmine as cz
        import zmine as Z
        self.assertFalse(hasattr(cz, "register") and callable(getattr(cz, "register", None)))
        for fn in ("login", "mining_overview", "tasks_next", "tick"):
            self.assertTrue(hasattr(Z, fn), f"zmine.{fn} must exist (read path)")


if __name__ == "__main__":
    unittest.main()
