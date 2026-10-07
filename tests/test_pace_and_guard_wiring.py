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
            # count, per-IP, inject?, store, egress  (no pace: the limit is a per-IP count)
            builtins.input = self._answers(["3", "3", "y", "both", "none"])
            out = kancahub._menu_ask_farm_options("8", ["thk", "batch"])
        finally:
            builtins.input = orig
        self.assertEqual(out, ["thk", "batch", "3", "--per-ip", "3", "--inject",
                               "--store", "both", "--proxy", "none"])

    def test_thk_defaults_inject_and_store_both(self):
        import builtins
        orig = builtins.input
        try:
            builtins.input = self._answers(["", "", "", "", ""])
            out = kancahub._menu_ask_farm_options("8", ["thk", "batch"])
        finally:
            builtins.input = orig
        self.assertEqual(out, ["thk", "batch", "1", "--per-ip", "4", "--inject",
                               "--store", "both", "--proxy", "auto"])

    def test_thk_per_ip_is_capped_at_five(self):
        import builtins
        orig = builtins.input
        try:
            builtins.input = self._answers(["9", "99", "n", "ledger", "none"])
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
        saved = [0]  # pretend harbor saves each chunk's accounts when its rc is 0
        def counter():
            return saved[0]
        def fake(cmd, **kw):
            calls.append(cmd[-1]); rc = next(it)
            if rc == 0:
                saved[0] += int(cmd[-1])
            return rc
        a = kancahub.build_parser().parse_args(argv)
        with patch.object(kancahub, "_choose_egress",
                          return_value=kancahub.EgressChoice(None, "none", direct=True)), \
                patch.object(kancahub, "run_with_mobile_retry", fake), \
                patch.object(kancahub, "run", lambda cmd, **kw: injected.append(cmd) or 0), \
                patch.object(kancahub, "_ledger_record"), \
                patch.object(kancahub.time, "sleep", lambda s: None), \
                patch.object(kancahub, "_current_ip", lambda: "1.1.1.1"), \
                patch.object(kancahub, "_thk_fresh_ip", lambda used, tries=3: True), \
                patch.object(kancahub, "_thk_account_count", counter), \
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

    def test_short_chunk_is_reported_not_claimed(self):
        # chunk returns rc 0 but harbor saved fewer accounts (throttled signups)
        from unittest.mock import patch
        a = kancahub.build_parser().parse_args(["thk", "batch", "4", "--per-ip", "2", "--mobile-rotate"])
        saved = [0]
        def fake(cmd, **kw):
            saved[0] += 1  # only 1 of 2 really created
            return 0
        with patch.object(kancahub, "_choose_egress",
                          return_value=kancahub.EgressChoice(None, "none", direct=True)), \
                patch.object(kancahub, "run_with_mobile_retry", fake), \
                patch.object(kancahub, "run", lambda *a, **k: 0), \
                patch.object(kancahub, "_ledger_record"), \
                patch.object(kancahub.time, "sleep", lambda s: None), \
                patch.object(kancahub, "_current_ip", lambda: "1.1.1.1"), \
                patch.object(kancahub, "_thk_fresh_ip", lambda used, tries=3: True), \
                patch.object(kancahub, "_thk_account_count", lambda: saved[0]), \
                patch.object(kancahub, "_stop_auto_gateways"):
            rc = kancahub.cmd_thk(a)
        # partial success: accounts exist so exit 0, but the count is the REAL one (2 saved, not 4)
        self.assertEqual(rc, 0)
        self.assertEqual(saved[0], 2)

    def test_thk_has_no_pace_flag(self):
        with self.assertRaises(SystemExit):
            kancahub.build_parser().parse_args(["thk", "batch", "2", "--pace", "normal"])


class TestThkClassify(unittest.TestCase):
    def test_reasons(self):
        c = kancahub.thk_classify
        self.assertEqual(c("x Signup failed: Too many sign-ups from this network. Please try again in an hour."), "netcap")
        self.assertEqual(c("You're doing that a bit fast - take a breath and try again."), "throttled")
        self.assertEqual(c("Your IP or email provider is not supported"), "blocked")
        self.assertEqual(c("Traceback boom"), "other")
        self.assertEqual(c(""), "other")

    def test_per_ip_default_is_four_max_five(self):
        self.assertEqual((kancahub.THK_PER_IP_DEFAULT, kancahub.THK_PER_IP_MAX), (4, 5))
        self.assertEqual(kancahub.thk_chunks(9), [4, 4, 1])


class TestThkNetcapStops(unittest.TestCase):
    def test_netcap_chunk_stops_batch_even_when_rc_zero(self):
        from unittest.mock import patch
        a = kancahub.build_parser().parse_args(["thk", "batch", "6", "--per-ip", "2", "--mobile-rotate"])
        calls = []
        def fake(cmd, **kw):
            calls.append(cmd[-1])
            kancahub._LAST_RUN_OUT["text"] = "Signup failed: Too many sign-ups from this network. Please try again in an hour."
            return 0  # harbor exits 0 only if something was made; here we simulate a late cap
        with patch.object(kancahub, "_choose_egress",
                          return_value=kancahub.EgressChoice(None, "none", direct=True)), \
                patch.object(kancahub, "run_with_mobile_retry", fake), \
                patch.object(kancahub, "run", lambda *a, **k: 0), \
                patch.object(kancahub, "_ledger_record"), \
                patch.object(kancahub.time, "sleep", lambda s: None), \
                patch.object(kancahub, "_current_ip", lambda: "1.1.1.1"), \
                patch.object(kancahub, "_thk_fresh_ip", lambda used, tries=3: True), \
                patch.object(kancahub, "_thk_account_count", lambda: 0), \
                patch.object(kancahub, "_stop_auto_gateways"):
            rc = kancahub.cmd_thk(a)
        self.assertEqual(calls, ["2"])   # did not retry a capped IP
        self.assertNotEqual(rc, 0)


class TestThkFreshIp(unittest.TestCase):
    def test_accepts_new_ip_and_records_it(self):
        from unittest.mock import patch
        used = {"1.1.1.1"}
        with patch.object(kancahub, "_mobile_rotate_once", lambda: "2.2.2.2"):
            self.assertTrue(kancahub._thk_fresh_ip(used))
        self.assertIn("2.2.2.2", used)

    def test_same_ip_three_times_fails(self):
        from unittest.mock import patch
        calls = []
        with patch.object(kancahub, "_mobile_rotate_once", lambda: calls.append(1) or "1.1.1.1"):
            self.assertFalse(kancahub._thk_fresh_ip({"1.1.1.1"}))
        self.assertEqual(len(calls), 3)

    def test_second_try_gets_new_ip(self):
        from unittest.mock import patch
        ips = iter(["1.1.1.1", "3.3.3.3"])
        with patch.object(kancahub, "_mobile_rotate_once", lambda: next(ips)):
            self.assertTrue(kancahub._thk_fresh_ip({"1.1.1.1"}))

    def test_no_phone_returns_false(self):
        from unittest.mock import patch
        with patch.object(kancahub, "_mobile_rotate_once", lambda: None):
            self.assertFalse(kancahub._thk_fresh_ip(set()))
