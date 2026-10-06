"""Tests for --pace presets and session-guard wiring in kancahub."""
from __future__ import annotations
from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import kancahub  # noqa: E402


class TestPacePresets(unittest.TestCase):
    def test_presets_exist(self):
        self.assertEqual(kancahub.PACE_PRESETS["normal"], (60.0, 90.0))
        self.assertIn("fast", kancahub.PACE_PRESETS)
        self.assertIn("safe", kancahub.PACE_PRESETS)

    def test_default_pace_is_normal(self):
        p = kancahub.build_parser()
        a = p.parse_args(["github", "farm"])
        m = kancahub.map_github_farm_args(a, None)
        self.assertEqual(m[m.index("--delay-min") + 1], "60.0")
        self.assertEqual(m[m.index("--delay-max") + 1], "90.0")

    def test_explicit_delay_overrides_pace(self):
        p = kancahub.build_parser()
        a = p.parse_args(["github", "farm", "--delay-min", "10", "--delay-max", "20"])
        m = kancahub.map_github_farm_args(a, None)
        self.assertEqual(m[m.index("--delay-min") + 1], "10.0")
        self.assertEqual(m[m.index("--delay-max") + 1], "20.0")


class TestGuardWiring(unittest.TestCase):
    def test_run_with_mobile_retry_accepts_account(self):
        import inspect
        sig = inspect.signature(kancahub.run_with_mobile_retry)
        self.assertIn("account", sig.parameters)

    def test_guard_helpers_exist(self):
        self.assertTrue(callable(kancahub._session_guard_start))
        self.assertTrue(callable(kancahub._session_guard_end))


if __name__ == "__main__":
    unittest.main()


class TestMenuFarmOptions(unittest.TestCase):
    """The menu asks for count/pace/egress IN the CLI (arg builder is pure-ish)."""
    def _answers(self, seq):
        import builtins
        it = iter(seq)
        return lambda *a, **k: next(it)

    def test_thk_builds_batch_n_and_egress(self):
        import builtins
        orig = builtins.input
        try:
            builtins.input = self._answers(["3", "normal", "none"])
            out = kancahub._menu_ask_farm_options("8", ["thk", "batch"])
        finally:
            builtins.input = orig
        self.assertEqual(out, ["thk", "batch", "3", "--proxy", "none"])

    def test_github_defaults(self):
        import builtins
        orig = builtins.input
        try:
            builtins.input = self._answers(["", "", ""])
            out = kancahub._menu_ask_farm_options("5", ["github", "farm"])
        finally:
            builtins.input = orig
        self.assertEqual(out, ["github", "farm", "--pace", "normal", "--proxy", "auto"])

    def test_mobile_maps_to_rotate(self):
        import builtins
        orig = builtins.input
        try:
            builtins.input = self._answers(["1", "normal", "mobile"])
            out = kancahub._menu_ask_farm_options("5", ["github", "farm"])
        finally:
            builtins.input = orig
        self.assertIn("--mobile-rotate", out)
