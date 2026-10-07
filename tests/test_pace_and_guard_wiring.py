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


class TestThkChunks(unittest.TestCase):
    def test_split(self):
        self.assertEqual(kancahub.thk_chunks(7, 3), [3, 3, 1])
        self.assertEqual(kancahub.thk_chunks(6, 3), [3, 3])
        self.assertEqual(kancahub.thk_chunks(1, 3), [1])
        self.assertEqual(kancahub.thk_chunks(0, 3), [])

    def test_cap_and_floor(self):
        self.assertEqual(kancahub.thk_chunks(10, 99), [5, 5])   # capped at 5/IP
        self.assertEqual(kancahub.thk_chunks(3, 0), [1, 1, 1])  # floor of 1

    def test_flags_parse(self):
        a = kancahub.build_parser().parse_args(
            ["thk", "batch", "4", "--per-ip", "2", "--inject", "--store", "9router"])
        self.assertEqual((a.count, a.per_ip, a.inject, a.store), (4, 2, True, "9router"))


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
            # count, per-IP, inject?, store, pace, egress
            builtins.input = self._answers(["3", "3", "y", "both", "normal", "none"])
            out = kancahub._menu_ask_farm_options("8", ["thk", "batch"])
        finally:
            builtins.input = orig
        self.assertEqual(out, ["thk", "batch", "3", "--per-ip", "3", "--inject",
                               "--store", "both", "--proxy", "none"])

    def test_thk_defaults_inject_and_store_both(self):
        import builtins
        orig = builtins.input
        try:
            builtins.input = self._answers(["", "", "", "", "", ""])
            out = kancahub._menu_ask_farm_options("8", ["thk", "batch"])
        finally:
            builtins.input = orig
        self.assertEqual(out, ["thk", "batch", "1", "--per-ip", "3", "--inject",
                               "--store", "both", "--proxy", "auto"])

    def test_thk_per_ip_is_capped_at_five(self):
        import builtins
        orig = builtins.input
        try:
            builtins.input = self._answers(["9", "99", "n", "ledger", "normal", "none"])
            out = kancahub._menu_ask_farm_options("8", ["thk", "batch"])
        finally:
            builtins.input = orig
        self.assertEqual(out[out.index("--per-ip") + 1], "5")
        self.assertNotIn("--inject", out)

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


class TestThkBatchLoop(unittest.TestCase):
    """cmd_thk batch: chunked per IP, stops on a failed chunk, injects only if something was made."""

    def _run(self, argv, rcs):
        from unittest.mock import patch
        calls, injected = [], []
        it = iter(rcs)
        a = kancahub.build_parser().parse_args(argv)
        with patch.object(kancahub, "_choose_egress",
                          return_value=kancahub.EgressChoice(None, "none", direct=True)), \
                patch.object(kancahub, "run_with_mobile_retry",
                             lambda cmd, **kw: calls.append(cmd[-1]) or next(it)), \
                patch.object(kancahub, "run", lambda cmd, **kw: injected.append(cmd) or 0), \
                patch.object(kancahub, "_ledger_record"), \
                patch.object(kancahub, "_stop_auto_gateways"):
            rc = kancahub.cmd_thk(a)
        return rc, calls, injected

    def test_chunks_then_inject(self):
        rc, calls, inj = self._run(["thk", "batch", "5", "--per-ip", "3", "--mobile-rotate", "--inject"], [0, 0])
        self.assertEqual((rc, calls), (0, ["3", "2"]))
        self.assertEqual(len(inj), 1)  # one bulk inject after all chunks

    def test_failed_chunk_stops_and_skips_inject(self):
        rc, calls, inj = self._run(["thk", "batch", "6", "--per-ip", "3", "--mobile-rotate", "--inject"], [1])
        self.assertNotEqual(rc, 0)
        self.assertEqual(calls, ["3"])  # did not hammer a burnt IP
        self.assertEqual(inj, [])       # nothing made -> nothing to inject

    def test_no_rotate_stops_at_cap(self):
        rc, calls, _ = self._run(["thk", "batch", "6", "--per-ip", "3"], [0])
        self.assertEqual(calls, ["3"])  # without --mobile-rotate it won't reuse the same IP
