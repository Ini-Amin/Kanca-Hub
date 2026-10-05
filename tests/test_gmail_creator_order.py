"""Regression test for tab initialization ordering in create_one."""

from __future__ import annotations

import inspect
from pathlib import Path
import sys
import unittest

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.gmail_creator import create_one


class TestGmailCreatorOrder(unittest.TestCase):
    def test_create_one_compiles(self):
        """create_one is importable and is a callable coroutine function."""
        self.assertTrue(callable(create_one))
        self.assertTrue(inspect.iscoroutinefunction(create_one))

    def test_tab_assigned_before_warm_session(self):
        """tab must be assigned before warm_session is called."""
        src = inspect.getsource(create_one)
        idx_tab_get = src.find("tab = await browser.get")
        idx_warm = src.find("warm_session(tab)")

        self.assertNotEqual(idx_tab_get, -1, "Expected 'tab = await browser.get' in create_one source")
        self.assertNotEqual(idx_warm, -1, "Expected 'warm_session(tab)' in create_one source")
        self.assertLess(
            idx_tab_get,
            idx_warm,
            "'tab = await browser.get' must occur before 'warm_session(tab)' to prevent UnboundLocalError",
        )


if __name__ == "__main__":
    unittest.main()
