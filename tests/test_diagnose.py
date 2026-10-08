"""Pure tests for scripts/diagnose.py classifiers (no network)."""
from __future__ import annotations
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


if __name__ == "__main__":
    unittest.main()