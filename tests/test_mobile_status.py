"""mobile status/rotate: verdict from raw phone facts; rotate refuses unless on mobile data."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

import mobile_rotate as mr  # noqa: E402


def facts(**kw):
    base = dict(airplane="0", mobile_data="1", sim="LOADED", data_reg_state="0", default_transport="CELLULAR")
    base.update(kw)
    return base


class TestClassify(unittest.TestCase):
    def test_ok_on_cellular(self):
        self.assertEqual(mr.classify_phone(**facts())[0], "ok")

    def test_empty_quota_shape_is_no_data(self):
        # what the Redmi reported: SIM loaded, data service out, default network Wi-Fi
        self.assertEqual(mr.classify_phone(**facts(data_reg_state="1", default_transport="WIFI"))[0], "no_data")

    def test_wifi_but_data_service_fine(self):
        self.assertEqual(mr.classify_phone(**facts(default_transport="WIFI"))[0], "wifi")

    def test_switched_off_cases(self):
        self.assertEqual(mr.classify_phone(**facts(airplane="1"))[0], "airplane")
        self.assertEqual(mr.classify_phone(**facts(mobile_data="0"))[0], "data_off")
        self.assertEqual(mr.classify_phone(**facts(sim="ABSENT"))[0], "no_sim")

    def test_unknown_when_no_signal(self):
        self.assertEqual(mr.classify_phone(**facts(default_transport="NONE", data_reg_state="0"))[0], "unknown")


class TestRotateGuard(unittest.TestCase):
    def _main(self, argv, verdict):
        calls = []
        st = {"verdict": verdict, "advice": "x", "operator": "", "sim": "", "default_transport": "",
              "data_reg_state": "", "mobile_data": "", "airplane": ""}
        with patch.object(sys, "argv", ["mobile_rotate.py"] + argv), \
                patch.object(mr, "first_device", lambda: "dev"), \
                patch.object(mr, "phone_state", lambda s: st), \
                patch.object(mr, "rotate", lambda *a, **k: calls.append(1) or "1.2.3.4"), \
                patch.object(mr, "get_ip", lambda: "9.9.9.9"):
            rc = mr.main()
        return rc, calls

    def test_refuses_when_not_on_mobile(self):
        rc, calls = self._main(["--rotate"], "no_data")
        self.assertEqual((rc, calls), (3, []))  # never toggled airplane mode

    def test_rotates_when_ok(self):
        rc, calls = self._main(["--rotate"], "ok")
        self.assertEqual((rc, len(calls)), (0, 1))

    def test_force_overrides(self):
        rc, calls = self._main(["--rotate", "--force"], "no_data")
        self.assertEqual((rc, len(calls)), (0, 1))

    def test_status_exit_code_reflects_verdict(self):
        self.assertEqual(self._main(["--status"], "ok")[0], 0)
        self.assertEqual(self._main(["--status"], "wifi")[0], 3)


class TestFarmRefusesOffMobile(unittest.TestCase):
    def test_run_with_mobile_retry_stops_when_phone_on_wifi(self):
        sys.path.insert(0, str(REPO))
        import kancahub
        ran = []
        with patch.object(kancahub, "_mobile_rotate_once", lambda wait=25.0: None), \
                patch.object(kancahub, "_phone_not_on_mobile", lambda: True), \
                patch.object(kancahub, "_run_capture", lambda *a, **k: ran.append(1) or (0, "")):
            rc = kancahub.run_with_mobile_retry(["x"], mobile_rotate=True)
        self.assertEqual((rc, ran), (3, []))  # farm command never started

    def test_no_phone_attached_does_not_block(self):
        sys.path.insert(0, str(REPO))
        import kancahub
        with patch.object(kancahub, "_mobile_rotate_once", lambda wait=25.0: None), \
                patch.object(kancahub, "_phone_not_on_mobile", lambda: False), \
                patch.object(kancahub, "_run_capture", lambda *a, **k: (0, "")):
            self.assertEqual(kancahub.run_with_mobile_retry(["x"], mobile_rotate=True), 0)


if __name__ == "__main__":
    unittest.main()
