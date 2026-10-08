"""Regression tests for autofarm venv + proxy-mode fixes."""
from __future__ import annotations
from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import autofarm


class TestAutofarmProxyResolution(unittest.TestCase):
    def test_none_forces_direct(self):
        self.assertIsNone(autofarm.get_fresh_proxy("none"))
        self.assertIsNone(autofarm.get_fresh_proxy(None, mode="none"))

    def test_explicit_url_used(self):
        self.assertEqual(autofarm.get_fresh_proxy("http://127.0.0.1:8888"), "http://127.0.0.1:8888")

    def test_has_no_proxy_flag(self):
        # the CLI must expose --no-proxy
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                autofarm.main.__wrapped__
            except Exception:
                pass
        # parser-level check
        src = (REPO_ROOT / "scripts" / "autofarm.py").read_text()
        self.assertIn("--no-proxy", src)
        self.assertIn("--domain", src)


class TestKancahubAutofarmWiring(unittest.TestCase):
    def test_uses_camoufox_venv(self):
        src = (REPO_ROOT / "scripts" / "kancahub.py").read_text()
        # cmd_autofarm must pick the camoufox python
        idx = src.find("def cmd_autofarm")
        seg = src[idx:idx + 400]
        self.assertIn("pick_python(camoufox=True)", seg,
                      "cmd_autofarm must run under the camoufox venv (has camoufox/playwright)")

    def test_autofarm_in_unified_menu(self):
        import kancahub
        self.assertIn("autofarm", {item[2] for item in kancahub.UNIFIED_MENU})


class TestBeginnerBrowserRouting(unittest.TestCase):
    def _fake_camoufox(self, tmp: str):
        from pathlib import Path
        p = Path(tmp) / "camoufox-venv" / "bin" / "python"
        p.parent.mkdir(parents=True)
        p.write_text("")
        return p

    def test_browser_scripts_use_camoufox_python(self):
        import tempfile
        from unittest.mock import patch
        import beginner
        with tempfile.TemporaryDirectory() as d:
            with patch.object(beginner, "CAMOUFOX_PY", self._fake_camoufox(d)):
                with patch("beginner.subprocess.call", return_value=0) as call:
                    beginner.run_script("autofarm.py", ["https://x.test"])
                cmd = call.call_args[0][0]
        self.assertIn("camoufox-venv", cmd[0],
                      "browser scripts must launch under the camoufox venv")

    def test_non_browser_script_uses_default(self):
        from unittest.mock import patch
        import beginner
        with patch("beginner.subprocess.call", return_value=0) as call:
            beginner.run_script("adb_tool.py", ["phone"])
        self.assertNotIn("camoufox-venv", call.call_args[0][0][0])


if __name__ == "__main__":
    unittest.main()
