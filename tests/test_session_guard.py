"""Tests for session_guard (CGNAT IP-stability) — no network."""
from __future__ import annotations
from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import session_guard as sg  # noqa: E402


class TestSessionGuard(unittest.TestCase):
    def test_stable_session(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            sg.guard_start("a@x", ip="1.2.3.4", path=p)
            r = sg.guard_end("a@x", ip="1.2.3.4", path=p)
            self.assertEqual(r["verdict"], "stable")
            self.assertTrue(r["stable"])

    def test_changed_session(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            sg.guard_start("a@x", ip="1.2.3.4", path=p)
            r = sg.guard_end("a@x", ip="5.6.7.8", path=p)
            self.assertEqual(r["verdict"], "changed")
            self.assertFalse(r["stable"])

    def test_unknown_when_no_ip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            sg.guard_start("a@x", ip=None, path=p)
            r = sg.guard_end("a@x", ip=None, path=p)
            self.assertEqual(r["verdict"], "unknown")

    def test_reuse_count(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "s.json"
            sg.guard_start("a@x", ip="1.1.1.1", path=p); sg.guard_end("a@x", ip="1.1.1.1", path=p)
            self.assertEqual(sg.reuse_count("1.1.1.1", path=p), 1)
            self.assertEqual(sg.reuse_count("9.9.9.9", path=p), 0)

    def test_report_empty(self):
        self.assertIn("no sessions", sg.per_ip_report("/nonexistent.json"))


if __name__ == "__main__":
    unittest.main()
