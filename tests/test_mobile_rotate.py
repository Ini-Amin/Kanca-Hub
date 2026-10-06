"""Tests for mobile_rotate helpers (no device needed)."""
from __future__ import annotations
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import mobile_rotate as mr


class TestMobileRotate(unittest.TestCase):
    def test_first_device_parses_adb(self):
        fake = "List of devices attached\nc8ec3d70\tdevice\n"
        with patch.object(mr, "_adb", return_value=fake):
            self.assertEqual(mr.first_device(), "c8ec3d70")

    def test_first_device_none(self):
        with patch.object(mr, "_adb", return_value="List of devices attached\n"):
            self.assertIsNone(mr.first_device())

    def test_get_ip_uses_ipify(self):
        class R:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b"1.2.3.4\n"
        with patch("urllib.request.urlopen", return_value=R()):
            self.assertEqual(mr.get_ip(), "1.2.3.4")


if __name__ == "__main__":
    unittest.main()
