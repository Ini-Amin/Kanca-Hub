"""Tests for the doctor egress-verdict helper (no network)."""
from __future__ import annotations
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import kancahub  # noqa: E402


class TestEgressVerdict(unittest.TestCase):
    def test_probe_http_returns_status(self):
        class R:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False
        with patch("urllib.request.urlopen", return_value=R()):
            self.assertEqual(kancahub._probe_http("https://x/"), 200)

    def test_probe_http_zero_on_error(self):
        with patch("urllib.request.urlopen", side_effect=OSError("boom")):
            self.assertEqual(kancahub._probe_http("https://x/"), 0)

    def test_verdict_mentions_github_hint_when_blocked(self):
        with patch.object(kancahub, "_probe_http", side_effect=lambda u, **k: 403 if "github" in u else 200), \
             patch("urllib.request.urlopen") as uo:
            uo.return_value.__enter__.return_value.read.return_value = b"1.2.3.4"
            out = kancahub._egress_verdict()
        self.assertIn("GitHub blocked", out)
        self.assertIn("kancahub mobile rotate", out)

    def test_verdict_handles_api_failure(self):
        with patch.object(kancahub, "_probe_http", return_value=200), \
             patch("urllib.request.urlopen", side_effect=OSError("no net")):
            out = kancahub._egress_verdict()
        self.assertIsInstance(out, str)


if __name__ == "__main__":
    unittest.main()
