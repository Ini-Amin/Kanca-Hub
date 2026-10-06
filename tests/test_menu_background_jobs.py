"""Tests for menu background jobs (JobRegistry) + menu wiring — no servers."""
from __future__ import annotations
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, MagicMock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import kancahub  # noqa: E402


class TestJobRegistry(unittest.TestCase):
    def test_running_and_alive(self):
        jr = kancahub.JobRegistry()
        p = MagicMock(); p.poll.return_value = None; p.pid = 123
        jr.jobs["g"] = p
        self.assertTrue(jr.alive("g"))
        self.assertEqual(jr.running(), ["g"])
        p.poll.return_value = 1
        self.assertFalse(jr.alive("g"))
        self.assertEqual(jr.running(), [])

    def test_menu_items_background(self):
        # proxy gateway + harvest must be background (never block the menu)
        self.assertIn("2", kancahub.MENU_BACKGROUND)
        self.assertIn("4", kancahub.MENU_BACKGROUND)
        # their commands must be non-interactive
        self.assertNotIn("start", kancahub.MENU_BACKGROUND_CMD["2"])
        self.assertEqual(kancahub.MENU_BACKGROUND_CMD["2"][0], "proxy")

    def test_stop_missing_is_ok(self):
        jr = kancahub.JobRegistry()
        self.assertEqual(jr.stop("nope"), 0)

    def test_stop_kills_group(self):
        jr = kancahub.JobRegistry()
        p = MagicMock(); p.poll.return_value = None; p.pid = 4242
        jr.jobs["g"] = p
        with patch("os.getpgid", return_value=4242), patch("os.killpg") as kg:
            jr.stop("g")
        kg.assert_called()
        self.assertNotIn("g", jr.jobs)


if __name__ == "__main__":
    unittest.main()
