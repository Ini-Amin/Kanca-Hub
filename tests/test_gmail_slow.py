"""Tests for scripts/gmail_slow.py drip scheduling (no browser/network)."""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import gmail_slow as G  # noqa: E402


class TestDrip(unittest.TestCase):
    def _isolated(self, d):
        return patch.object(G, "STATE", Path(d) / "state.json")

    def test_first_run_is_due(self):
        with tempfile.TemporaryDirectory() as d, self._isolated(d):
            due, wait = G._due(3.5)
            self.assertTrue(due)
            self.assertEqual(wait, 0.0)

    def test_not_due_after_recent_create(self):
        with tempfile.TemporaryDirectory() as d, self._isolated(d):
            G._save({"last_created_ts": time.time(), "count": 1})
            due, wait = G._due(3.5)
            self.assertFalse(due)
            self.assertGreater(wait, 3 * 86400)

    def test_due_after_interval(self):
        with tempfile.TemporaryDirectory() as d, self._isolated(d):
            G._save({"last_created_ts": time.time() - 4 * 86400})
            due, _ = G._due(3.5)
            self.assertTrue(due)

    def test_reset_clears(self):
        with tempfile.TemporaryDirectory() as d, self._isolated(d):
            G._save({"last_created_ts": time.time()})
            G._save({})
            due, _ = G._due(3.5)
            self.assertTrue(due)

    def test_human_format(self):
        self.assertEqual(G._human(0), "now")
        self.assertIn("3d", G._human(3 * 86400 + 3600))


if __name__ == "__main__":
    unittest.main()