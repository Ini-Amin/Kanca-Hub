"""Regression tests for the opt-in access_restricted retry helpers.

Covers scripts/github_farm.py:
  - should_retry_access_restricted(): full truth table, incl. the guarantee
    that default behavior (retries=0) never retries.
  - next_pool_proxy(): pure next-proxy selection over the pool.
  - run() keeps `retries=0` as its default.

Pure unit tests — no browser, no network, stdlib unittest only.
"""

from __future__ import annotations

import inspect
from pathlib import Path
import sys
import unittest

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.github_farm import (
    next_pool_proxy,
    run,
    should_retry_access_restricted,
)

class TestShouldRetryAccessRestricted(unittest.TestCase):
    """Truth table for the retry decision."""

    def test_default_retries_zero_never_retries(self):
        # Default behavior must remain: one attempt, exactly as before.
        for attempt in (1, 2, 10):
            self.assertFalse(
                should_retry_access_restricted("access_restricted", attempt, 0, True),
                "retries=0 must never retry",
            )

    def test_optin_with_pool_retries_until_budget_exhausted(self):
        retries = 3
        self.assertTrue(should_retry_access_restricted("access_restricted", 1, retries, True))
        self.assertTrue(should_retry_access_restricted("access_restricted", 2, retries, True))
        self.assertTrue(should_retry_access_restricted("access_restricted", 3, retries, True))
        self.assertFalse(should_retry_access_restricted("access_restricted", 4, retries, True))

    def test_single_retry_budget(self):
        # --retries 1 == one extra attempt after the initial one.
        self.assertTrue(should_retry_access_restricted("access_restricted", 1, 1, True))
        self.assertFalse(should_retry_access_restricted("access_restricted", 2, 1, True))

    def test_no_pool_never_retries(self):
        for attempt in (1, 2, 3):
            self.assertFalse(should_retry_access_restricted("access_restricted", attempt, 5, False))

    def test_other_stages_never_retry(self):
        for stage in ("start", "email_filled", "password_filled",
                      "captcha_after_email", "captcha_after_password",
                      "email_input_not_found", "success", "", None):
            self.assertFalse(
                should_retry_access_restricted(stage, 1, 5, True),
                f"stage={stage!r} must not trigger a retry",
            )

    def test_defensive_bad_inputs(self):
        self.assertFalse(should_retry_access_restricted("access_restricted", 0, 5, True))
        self.assertFalse(should_retry_access_restricted("access_restricted", -1, 5, True))
        self.assertFalse(should_retry_access_restricted("access_restricted", 1, -1, True))

class TestNextPoolProxy(unittest.TestCase):
    """Pure next-proxy selection over the pool."""

    def test_empty_pool(self):
        self.assertIsNone(next_pool_proxy([], [], 1))

    def test_all_used_returns_none(self):
        self.assertIsNone(next_pool_proxy(["a", "b"], ["a", "b"], 1))
        self.assertIsNone(next_pool_proxy(["a"], ["a"], 3))

    def test_skips_used_and_returns_next_unused(self):
        self.assertEqual(next_pool_proxy(["a", "b", "c"], ["a"], 1), "b")
        self.assertEqual(next_pool_proxy(["a", "b", "c"], ["a", "b"], 1), "c")
        self.assertEqual(next_pool_proxy(["a", "b", "c"], ["b"], 1), "a")

    def test_rotation_is_deterministic_by_index(self):
        pool = ["a", "b", "c"]
        self.assertEqual(next_pool_proxy(pool, [], 1), "a")
        self.assertEqual(next_pool_proxy(pool, [], 2), "b")
        self.assertEqual(next_pool_proxy(pool, [], 3), "c")
        self.assertEqual(next_pool_proxy(pool, [], 4), "a")  # wraps around

class TestRunDefaults(unittest.TestCase):
    """run() must keep retries=0 as the default (unchanged behavior)."""

    def test_run_retries_default_is_zero(self):
        sig = inspect.signature(run)
        self.assertIn("retries", sig.parameters)
        self.assertEqual(sig.parameters["retries"].default, 0)

if __name__ == "__main__":
    unittest.main()
