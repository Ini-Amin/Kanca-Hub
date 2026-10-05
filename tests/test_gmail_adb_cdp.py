"""Tests for the ADB/CDP handshake fix in scripts/gmail_adb.py (no device needed)."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.gmail_adb import (
    CDP, WEB, CLICK_FALLBACKS, click_text_js, select_material_js, select_material,
)


class TestMaterialDropdown(unittest.TestCase):
    def test_select_material_js_targets_visible_options_with_data_value(self):
        js = select_material_js("#gender", "1")
        self.assertIn("role=option", js)
        self.assertIn("data-value", js)
        self.assertIn('"1"', js)

    def test_select_material_opens_with_real_mouse_then_clicks_option(self):
        """select_material must open via CDP mouse (not JS .click) then pick option."""
        cdp = MagicMock()
        # first js() call returns the element rect; second returns 'ok'
        cdp.js = MagicMock(side_effect=[
            '{"x":196,"y":316}',
            "ok",
        ])
        result = select_material(cdp, "#gender", "1")
        self.assertEqual(result, "ok")
        cdp.click_at.assert_called_once_with(196, 316)

    def test_select_material_no_dropdown(self):
        cdp = MagicMock()
        cdp.js = MagicMock(return_value=None)
        self.assertEqual(select_material(cdp, "#month", "5"), "no-dropdown")


class TestI18nAndUrl(unittest.TestCase):
    def test_signup_url_forces_english(self):
        """The signup URL must carry hl=en so buttons read 'Next', not 'Berikutnya'."""
        self.assertIn("hl=en", WEB)

    def test_click_text_js_includes_localised_fallbacks(self):
        """click_text_js('next') must match 'Berikutnya' (Indonesian) too."""
        js = click_text_js("next")
        self.assertIn("berikutnya", js.lower())
        self.assertIn("next", js.lower())
        # a canonical label with no fallback still works
        js2 = click_text_js("some custom label")
        self.assertIn("some custom label", js2.lower())

    def test_fallback_table_covers_core_buttons(self):
        for label in ("next", "skip", "i agree", "agree"):
            self.assertIn(label, CLICK_FALLBACKS)
            self.assertIn(label, CLICK_FALLBACKS[label])


class TestCdpSuppressOrigin(unittest.TestCase):
    def test_passes_suppress_origin(self):
        """CDP.__init__ must ask websocket-client to suppress the Origin header.

        Modern Chrome rejects the CDP WebSocket handshake (403) when an Origin
        header is present and the browser was not launched with
        --remote-allow-origins. Suppressing the Origin is the client-side fix.
        """
        fake_ws = MagicMock()
        with patch("websocket.create_connection", return_value=fake_ws) as m:
            cdp = CDP("ws://127.0.0.1:9222/devtools/page/1")

        # create_connection must have been called WITH suppress_origin=True.
        self.assertTrue(m.called)
        _, kwargs = m.call_args
        self.assertIs(kwargs.get("suppress_origin"), True)
        self.assertIs(cdp.ws, fake_ws)

    def test_falls_back_without_suppress_origin_on_typeerror(self):
        """If websocket-client is too old (no suppress_origin kwarg), fall back."""
        fake_ws = MagicMock()
        calls = {"n": 0}

        def side_effect(*args, **kwargs):
            calls["n"] += 1
            if "suppress_origin" in kwargs:
                raise TypeError("create_connection() got an unexpected keyword argument 'suppress_origin'")
            return fake_ws

        with patch("websocket.create_connection", side_effect=side_effect) as m:
            cdp = CDP("ws://127.0.0.1:9222/devtools/page/1")

        self.assertEqual(calls["n"], 2, "should retry once without suppress_origin")
        # second call must NOT include suppress_origin
        _, second_kwargs = m.call_args
        self.assertNotIn("suppress_origin", second_kwargs)
        self.assertIs(cdp.ws, fake_ws)


class TestStateDetectorSource(unittest.TestCase):
    """Guards against the birthday/phone false-positive that stalled every run."""

    def _state_src(self):
        import inspect
        import scripts.gmail_adb as m
        return inspect.getsource(m.state)

    def test_birthday_checked_before_generic_tel(self):
        src = self._state_src()
        # match the actual return statements, not comments mentioning the words
        i_birthday = src.find("return 'birthday'")
        i_phone = src.find("return 'phone'")
        self.assertNotEqual(i_birthday, -1, "state() must return 'birthday'")
        self.assertNotEqual(i_phone, -1, "state() must return 'phone'")
        # the birthday branch must be decided before the generic tel->phone branch
        self.assertLess(i_birthday, i_phone,
                        "birthday must be returned BEFORE the generic input[type=tel] phone check")


class TestPasswordConfirmSelector(unittest.TestCase):
    def test_run_one_handles_passwdagain(self):
        import inspect
        import scripts.gmail_adb as m
        src = inspect.getsource(m.run_one)
        self.assertIn("PasswdAgain", src,
                      "the current flow names the confirm field PasswdAgain")


if __name__ == "__main__":
    unittest.main()
