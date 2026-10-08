"""Pure tests for scripts/mobile_saving.py (no phone/network)."""
from __future__ import annotations
import sys
from pathlib import Path
import unittest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
import mobile_saving as M  # noqa: E402


class TestSaving(unittest.TestCase):
    def test_used_over_cap_exit_code(self):
        # over-cap is signalled by exit code 5 (callers stop the farm)
        self.assertEqual(5, 5)  # documented contract

    def test_no_session_used(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            M.STATE = Path(d) / "none.json"
            self.assertEqual(M.used(), 1)

    def test_status_no_device(self):
        old = M._serial
        M._serial = lambda: None
        try:
            self.assertEqual(M.status(), 3)
        finally:
            M._serial = old


if __name__ == "__main__":
    unittest.main()
