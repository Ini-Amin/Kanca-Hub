"""Tests for kancahub 9router combo management."""
from __future__ import annotations
from pathlib import Path
import sys, unittest
from unittest.mock import patch
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts"))
import kancahub  # noqa: E402

class TestCombo(unittest.TestCase):
    def test_list_parses(self):
        p = kancahub.build_parser()
        a = p.parse_args(["9router", "combo", "list"])
        self.assertEqual(a.action, "list")
        with patch.object(kancahub, "_r9_api", return_value=(200, '{"combos":[{"name":"x","models":["a"],"id":"1"}]}')):
            self.assertEqual(kancahub.cmd_9router(a), 0)

    def test_make_creates(self):
        p = kancahub.build_parser()
        a = p.parse_args(["9router", "combo", "make-thk-fallback"])
        seen = {}
        def fake(m, path, *, body=None, base=""):
            seen["body"] = body; return 201, '{"id":"z"}'
        with patch.object(kancahub, "_r9_api", side_effect=fake):
            self.assertEqual(kancahub.cmd_9router(a), 0)
        self.assertEqual(seen["body"]["name"], "thk-fallback")
        self.assertTrue(seen["body"]["models"][0].startswith("THK/"))

if __name__ == "__main__":
    unittest.main()
