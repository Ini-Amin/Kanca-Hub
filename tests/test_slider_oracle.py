"""Regression: the shared slider solver must use a TRUSTWORTHY pass/fail oracle.

On z.ai the Aliyun SDK's success toast is "Slide successful!" and z.ai's own
success callback immediately refreshes the widget, so the puzzle window stays
VISIBLE. captcha_slider._passed() looks for "verification passed" or a hidden
window, so it returns False on a REAL pass -- which made solve_aliyun abandon a
passed captcha and abort the whole signup. slider_autolabel.py documented this
and worked around it with an SDK-callback + verify-API oracle; solve_aliyun is
now wired to the same signals via _SliderOracle / _verdict.

These tests exercise the parsing/verdict logic without a browser.
"""
import asyncio
import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SLIDER = REPO / "scripts" / "captcha_slider.py"


def load_slider():
    spec = importlib.util.spec_from_file_location("captcha_slider", SLIDER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["captcha_slider"] = mod
    spec.loader.exec_module(mod)
    return mod


CS = load_slider()


class _Resp:
    def __init__(self, url, body, method="POST"):
        self.url = url
        self._body = body
        self.request = type("Req", (), {"method": method, "post_data": ""})()

    async def text(self):
        return self._body


class _FakePage:
    """Minimal page double: records listeners, returns scripted JS results."""

    def __init__(self, inner_text=""):
        self._listeners = []
        self._inner_text = inner_text
        self._init_scripts = []

    def on(self, event, cb):
        self._listeners.append((event, cb))

    async def add_init_script(self, js):
        self._init_scripts.append(js)

    async def evaluate(self, js, *a):
        if "innerText" in js:
            return self._inner_text
        if "getElementById" in js:  # _passed(): window gone -> False here
            return False
        if "__capOracle" in js:  # snapshot read
            return {"npass": 0, "nfail": 0, "nerr": 0, "last": None}
        return None

    async def wait_for_timeout(self, ms):
        return None


def _verdict_response(verify_result=None, code=""):
    import json as _j
    body = _j.dumps({"Result": {"VerifyResult": verify_result, "VerifyCode": code}})
    return _Resp("https://x-verify.captcha-open.aliyuncs.com/verify", body)


class TestVerifyApiParsing(unittest.TestCase):
    def _events(self, resp):
        page = _FakePage()
        orc = CS._SliderOracle(page)
        asyncio.run(orc._on_response(resp))
        return orc.events

    def test_pass_code_T001(self):
        ev = self._events(_verdict_response(True, "T001"))
        self.assertEqual(len(ev), 1)
        self.assertTrue(ev[0]["ok"])

    def test_fail_code_F015(self):
        ev = self._events(_verdict_response(False, "F015"))
        self.assertEqual(len(ev), 1)
        self.assertFalse(ev[0]["ok"])

    def test_bool_only(self):
        self.assertTrue(self._events(_verdict_response(True))[0]["ok"])
        self.assertFalse(self._events(_verdict_response(False))[0]["ok"])

    def test_code_only_T_is_pass(self):
        ev = self._events(_verdict_response(None, "T001"))
        self.assertTrue(ev[0]["ok"])
        ev = self._events(_verdict_response(None, "F015"))
        self.assertFalse(ev[0]["ok"])

    def test_non_verdict_response_ignored(self):
        # InitCaptchaV3 has no verdict -> must not be recorded as a fail.
        ev = self._events(_verdict_response(None, ""))
        self.assertEqual(ev, [])

    def test_other_url_ignored(self):
        r = _verdict_response(True, "T001")
        r.url = "https://chat.z.ai/api/v1/auths/signup"
        self.assertEqual(self._events(r), [])


class TestVerdictLogic(unittest.TestCase):
    def test_success_toast_text_is_a_pass(self):
        # The real SDK toast, which the legacy _passed() did NOT match.
        page = _FakePage(inner_text="Slide successful!")
        orc = CS._SliderOracle(page)
        before = {"pass": 0, "fail": 0, "net": 0}
        self.assertTrue(asyncio.run(CS._verdict(page, orc, before, timeout=0.5)))

    def test_verify_api_pass_wins(self):
        page = _FakePage()
        orc = CS._SliderOracle(page)
        orc.events.append({"t": 0, "ok": True, "code": "T001"})
        before = {"pass": 0, "fail": 0, "net": 0}
        self.assertTrue(asyncio.run(CS._verdict(page, orc, before, timeout=0.5)))

    def test_verify_api_fail_wins(self):
        page = _FakePage()
        orc = CS._SliderOracle(page)
        orc.events.append({"t": 0, "ok": False, "code": "F015"})
        before = {"pass": 0, "fail": 0, "net": 0}
        self.assertFalse(asyncio.run(CS._verdict(page, orc, before, timeout=0.5)))

    def test_no_signal_times_out_false(self):
        page = _FakePage()
        orc = CS._SliderOracle(page)
        before = {"pass": 0, "fail": 0, "net": 0}
        self.assertFalse(asyncio.run(CS._verdict(page, orc, before, timeout=0.3)))

    def test_stale_events_before_baseline_ignored(self):
        # A pass already recorded before this drag must not be re-counted.
        page = _FakePage()
        orc = CS._SliderOracle(page)
        orc.events.append({"t": 0, "ok": True, "code": "T001"})
        before = {"pass": 0, "fail": 0, "net": 1}  # we already saw it
        self.assertFalse(asyncio.run(CS._verdict(page, orc, before, timeout=0.3)))


if __name__ == "__main__":
    unittest.main()
