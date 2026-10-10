"""Tests for scripts/r9_keyguard.py — healthy-key counting + farm trigger."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import r9_keyguard as kg  # noqa: E402

NOW = 1_760_000_000_000  # fixed ms so timestamp math is deterministic


def iso(offset_ms: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp((NOW + offset_ms) / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def conn(**kw) -> dict:
    base = {"id": "x", "name": "n", "isActive": True, "testStatus": "active", "errorCode": None}
    base.update(kw)
    return base


class TestClassify(unittest.TestCase):
    def test_healthy(self):
        self.assertEqual(kg.classify(conn(), NOW), "healthy")

    def test_inactive_wins(self):
        self.assertEqual(kg.classify(conn(isActive=False, errorCode=429), NOW), "inactive")

    def test_dead_codes(self):
        for code in (401, 402, 403, 404):
            self.assertEqual(kg.classify(conn(errorCode=code), NOW), "dead", code)

    def test_429_is_quota_not_dead(self):
        # the whole point: a 429 key must stop counting as healthy but is
        # expected to come back, so it is "quota", not "dead".
        self.assertEqual(kg.classify(conn(errorCode=429), NOW), "quota")

    def test_model_lock_in_future_is_quota(self):
        c = conn(modelLock_mimo="2099-01-01T00:00:00Z")
        self.assertEqual(kg.classify(c, NOW), "quota")

    def test_expired_model_lock_stays_healthy(self):
        c = conn(modelLock_mimo=iso(-60_000))
        self.assertEqual(kg.classify(c, NOW), "healthy")

    def test_rate_limited_until_future_is_quota(self):
        self.assertEqual(kg.classify(conn(rateLimitedUntil=iso(30_000)), NOW), "quota")

    def test_teststatus_unavailable_is_quota(self):
        self.assertEqual(kg.classify(conn(testStatus="unavailable"), NOW), "quota")


class TestActiveLockUntil(unittest.TestCase):
    def test_picks_earliest_future(self):
        c = {"modelLock_a": iso(90_000), "modelLock_b": iso(10_000), "modelLock_c": iso(-5_000)}
        self.assertEqual(kg.active_lock_until(c, NOW), NOW + 10_000)

    def test_no_locks(self):
        self.assertEqual(kg.active_lock_until({}, NOW), 0.0)
        self.assertEqual(kg.active_lock_until(None, NOW), 0.0)

    def test_epoch_millis_form(self):
        self.assertEqual(kg._parse_ts(NOW + 5_000), NOW + 5_000)


class TestSummarize(unittest.TestCase):
    def test_counts(self):
        conns = [
            conn(), conn(),
            conn(errorCode=429, name="a"),
            conn(errorCode=402, name="b"),
            conn(isActive=False, name="c"),
        ]
        s = kg.summarize(conns, NOW)
        self.assertEqual(s["healthy"], 2)
        self.assertEqual(s["counts"], {"healthy": 2, "quota": 1, "dead": 1, "inactive": 1})
        self.assertEqual(s["by_error"], {"429": 1, "402": 1})

    def test_empty(self):
        self.assertEqual(kg.summarize([], NOW)["healthy"], 0)
        self.assertEqual(kg.summarize(None, NOW)["healthy"], 0)


class TestShouldFarm(unittest.TestCase):
    def test_boundary(self):
        self.assertTrue(kg.should_farm(2, 2))
        self.assertTrue(kg.should_farm(1, 2))
        self.assertTrue(kg.should_farm(0, 2))
        self.assertFalse(kg.should_farm(3, 2))
        self.assertFalse(kg.should_farm(18, 2))


class TestCooldown(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()) / "state.json"

    def test_roundtrip(self):
        kg.save_state(kg.mark_farmed({}, 2, now=1000.0), self.tmp)
        self.assertEqual(kg.load_state(self.tmp)["lastFarmHealthyCount"], 2)

    def test_cooldown_counts_down(self):
        st = kg.mark_farmed({}, 2, now=1000.0)
        self.assertEqual(kg.cooldown_left(st, 3600, now=1000.0), 3600)
        self.assertEqual(kg.cooldown_left(st, 3600, now=4600.0), 0.0)
        self.assertEqual(kg.cooldown_left(st, 3600, now=2000.0), 2600)

    def test_missing_state_means_no_cooldown(self):
        self.assertEqual(kg.cooldown_left({}, 3600, now=5000.0), 0.0)

    def test_reset_removes_file(self):
        kg.save_state(kg.mark_farmed({}, 1), self.tmp)
        self.assertTrue(self.tmp.exists())
        self.tmp.unlink()
        self.assertEqual(kg.cooldown_left(kg.load_state(self.tmp), 3600), 0.0)


class TestCliToken(unittest.TestCase):
    def test_env_override(self):
        import os

        old = os.environ.get("R9_TOKEN")
        os.environ["R9_TOKEN"] = "  deadbeefdeadbeef  "
        try:
            self.assertEqual(kg.cli_token(Path("/nonexistent")), "deadbeefdeadbeef")
        finally:
            if old is None:
                os.environ.pop("R9_TOKEN", None)
            else:
                os.environ["R9_TOKEN"] = old

    def test_missing_files_gives_empty(self):
        self.assertEqual(kg.cli_token(Path("/nonexistent")), "")


class TestParser(unittest.TestCase):
    def test_status_defaults(self):
        a = kg.build_parser().parse_args(["status"])
        self.assertEqual(a.threshold, 2)
        self.assertEqual(a.base, "http://localhost:20128")

    def test_watch_flags(self):
        a = kg.build_parser().parse_args(["watch", "--interval", "60", "--cooldown", "120", "--threshold", "3"])
        self.assertEqual((a.interval, a.cooldown, a.threshold), (60.0, 120.0, 3))

    def test_reset_tolerates_shared_flags(self):
        # kancahub passes --base/--threshold to every subcommand, so reset must
        # accept (and ignore) them rather than dying on argparse.
        a = kg.build_parser().parse_args(["reset", "--base", "http://x:1", "--threshold", "2"])
        self.assertEqual(a.threshold, 2)


class TestQuotaReason(unittest.TestCase):
    def test_reads_error_text(self):
        c = {"errorCode": 429, "lastError": "Your free daily quota for this model is used up for today"}
        self.assertEqual(kg.quota_reason(c), "quota exhausted")

    def test_generic_429(self):
        self.assertEqual(kg.quota_reason({"errorCode": 429}), "rate limited (429)")


class TestRender(unittest.TestCase):
    def test_includes_counts(self):
        s = kg.summarize([conn(), conn(errorCode=429, name="zed")], NOW)
        out = "\n".join(kg.render_status(s, threshold=2))
        self.assertIn("healthy keys : 1", out)
        self.assertIn("farm at <= 2", out)
        self.assertIn("zed", out)


if __name__ == "__main__":
    unittest.main()