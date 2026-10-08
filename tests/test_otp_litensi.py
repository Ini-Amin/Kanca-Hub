"""Pure-helper tests for scripts/otp_litensi.py (no network)."""
from __future__ import annotations

import sys
from pathlib import Path
import unittest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import otp_litensi as L  # noqa: E402


class TestExtractCode(unittest.TestCase):
    def test_github_dashed(self):
        self.assertEqual(L.extract_code("Your code is 1234-5678 now"), "12345678")

    def test_six_digit(self):
        self.assertEqual(L.extract_code("code: 998877"), "998877")

    def test_html_tags_stripped(self):
        self.assertEqual(L.extract_code("<p>Code <b>4321</b></p>"), "4321")

    def test_none(self):
        self.assertIsNone(L.extract_code("no numbers here"))
        self.assertIsNone(L.extract_code(""))


class TestRequiresCreds(unittest.TestCase):
    def test_missing_credentials_raises(self):
        import os
        from unittest.mock import patch
        for k in ("LITENSI_API_ID", "LITENSI_API_KEY", "LITENSI_SITE"):
            os.environ.pop(k, None)
        # ignore the real ~/.config/auto-freecf/.env in the isolated test
        with patch.object(L, "_env", return_value={}):
            with self.assertRaises(L.LitensiError):
                L.LitensiClient(api_id="", api_key="")


if __name__ == "__main__":
    unittest.main()