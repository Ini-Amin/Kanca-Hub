"""Tests for farm_ledger (append-only JSONL)."""
from __future__ import annotations
from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import farm_ledger as fl  # noqa: E402


class TestFarmLedger(unittest.TestCase):
    def test_record_and_read_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "l.jsonl"
            fl.record("thk", stage="created", ok=True, count=1, egress="mobile", path=p)
            fl.record("github", stage="blocked:403", ok=False, path=p)
            rows = fl.read(p)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["farm"], "thk")
            self.assertTrue(rows[0]["ok"])
            self.assertFalse(rows[1]["ok"])

    def test_read_missing_is_empty(self):
        self.assertEqual(fl.read("/nonexistent/x.jsonl"), [])

    def test_read_skips_bad_lines(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "l.jsonl"
            p.write_text('{"farm":"a"}\nnot-json\n{"farm":"b"}\n')
            rows = fl.read(p)
            self.assertEqual([r["farm"] for r in rows], ["a", "b"])

    def test_summary_counts(self):
        rows = [
            {"farm": "thk", "ok": True, "count": 1, "stage": "created"},
            {"farm": "github", "ok": False, "count": 0, "stage": "blocked:403"},
        ]
        s = fl.summarize(rows)
        self.assertIn("thk", s)
        self.assertIn("TOTAL", s)
        self.assertIn("blocked:403", s)

    def test_summary_empty(self):
        self.assertIn("no farm runs", fl.summarize([]))

    def test_record_never_raises_on_bad_path(self):
        # unwritable path -> still returns an entry, no exception
        e = fl.record("x", stage="test", path="/proc/nope/ledger.jsonl")
        self.assertEqual(e["farm"], "x")


if __name__ == "__main__":
    unittest.main()
