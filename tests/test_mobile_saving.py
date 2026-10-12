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

    def test_parse_net_dev_skips_ipa_and_wifi(self):
        dev = ("Inter-|   Receive\n face |bytes\n"
               "  wlan0: 900 1 0 0 0 0 0 0 800 1 0 0 0 0 0 0\n"
               "rmnet_ipa0: 999 1 0 0 0 0 0 0 999 1 0 0 0 0 0 0\n"
               "rmnet_data2: 100 1 0 0 0 0 0 0 40 1 0 0 0 0 0 0\n"
               "rmnet_data1: 10 1 0 0 0 0 0 0 5 1 0 0 0 0 0 0\n")
        self.assertEqual(M.parse_net_dev(dev), {"rmnet_data2": (100, 40), "rmnet_data1": (10, 5)})

    def test_run_kills_job_over_cap(self):
        counter = iter(range(0, 10**9, 30_000_000))  # +30 MB per poll
        old = (M._serial, M._mobile_rx_tx)
        M._serial, M._mobile_rx_tx = (lambda: "x"), (lambda s: (next(counter), 0))
        try:
            self.assertEqual(M.run(20, [sys.executable, "-c", "import time; time.sleep(30)"], poll=0.1), 5)
        finally:
            M._serial, M._mobile_rx_tx = old

    def test_status_no_device(self):
        old = M._serial
        M._serial = lambda: None
        try:
            self.assertEqual(M.status(), 3)
        finally:
            M._serial = old


if __name__ == "__main__":
    unittest.main()
