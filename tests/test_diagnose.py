"""Pure tests for scripts/diagnose.py classifiers (no network)."""
from __future__ import annotations
from contextlib import redirect_stdout
import io
import json
import sys
from pathlib import Path
import unittest
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
import diagnose as D  # noqa: E402


class TestEgress(unittest.TestCase):
    def test_flagged_proxy(self):
        self.assertEqual(D.classify_egress({"proxy": True})[0], "FLAGGED")

    def test_datacenter_is_weak(self):
        self.assertEqual(D.classify_egress({"hosting": True})[0], "WEAK")

    def test_mobile_is_strong(self):
        self.assertEqual(D.classify_egress({"mobile": True})[0], "STRONG")

    def test_residential_ok(self):
        self.assertEqual(D.classify_egress({})[0], "OK")


class TestGithub(unittest.TestCase):
    def test_403_blocked(self):
        self.assertEqual(D.classify_github(403)[0], "BLOCKED")

    def test_200_ok(self):
        self.assertEqual(D.classify_github(200)[0], "OK")


class TestPhone(unittest.TestCase):
    def test_verdicts(self):
        self.assertEqual(D.classify_phone("ok")[0], "READY")
        self.assertEqual(D.classify_phone("no_data")[0], "EMPTY")
        self.assertEqual(D.classify_phone("wifi")[0], "OFF")


class TestSummarize(unittest.TestCase):
    def test_counts_blockers(self):
        n, _ = D.summarize({"a": ("BLOCKED", ""), "b": ("OK", ""), "c": ("EMPTY", "")})
        self.assertEqual(n, 2)

    def test_summarize_returns_blocked_count(self):
        checks = {
            "c1": ("BLOCKED", "blocked msg"),
            "c2": ("FLAGGED", "flagged msg"),
            "c3": ("EMPTY", "empty msg"),
            "c4": ("MISSING", "missing msg"),
            "c5": ("NONE", "none msg"),
            "c6": ("OK", "ok msg"),
            "c7": ("DOWN", "down msg"),
        }
        blocked, lines = D.summarize(checks)
        self.assertEqual(blocked, 5)
        self.assertEqual(len(lines), 7)


class TestJsonMode(unittest.TestCase):
    def test_summarize_blocked_count_and_json_shape_stable(self):
        checks = {
            "litensi-otp": ("OK", "keys present"),
            "proxy-pool": ("BLOCKED", "need proxy"),
            "sms-webhook": ("EMPTY", "no tunnel"),
        }
        blocked_count, _ = D.summarize(checks)
        self.assertEqual(blocked_count, 2)

        data = D.to_json(checks)
        self.assertEqual(
            data,
            {
                "litensi-otp": {"status": "OK", "note": "keys present"},
                "proxy-pool": {"status": "BLOCKED", "note": "need proxy"},
                "sms-webhook": {"status": "EMPTY", "note": "no tunnel"},
            },
        )
        for val in data.values():
            self.assertEqual(set(val.keys()), {"status", "note"})

    def test_cli_json_mode_runs_and_shape_is_stable(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = D.main(["--json"])
        self.assertEqual(ret, 0)
        parsed = json.loads(buf.getvalue())
        self.assertIsInstance(parsed, dict)
        self.assertGreater(len(parsed), 0)
        for name, entry in parsed.items():
            self.assertIsInstance(name, str)
            self.assertIsInstance(entry, dict)
            self.assertEqual(set(entry.keys()), {"status", "note"})
            self.assertIsInstance(entry["status"], str)
            self.assertIsInstance(entry["note"], str)


if __name__ == "__main__":
    unittest.main()