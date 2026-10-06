"""Tests for the background proxy-gateway spawn/stop helpers (no server)."""
from __future__ import annotations
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import kancahub  # noqa: E402


class TestBackgroundProxy(unittest.TestCase):
    def test_spawn_writes_pid_and_returns_zero(self):
        with tempfile.TemporaryDirectory() as d:
            bgdir = Path(d)
            with patch.object(kancahub, "BACKGROUND_DIR", bgdir), \
                 patch("subprocess.Popen") as popen:
                proc = MagicMock(); proc.pid = 4321; proc.poll.return_value = None
                popen.return_value = proc
                rc = kancahub._spawn_background(["sleep", "999"], name="gateway9999")
            self.assertEqual(rc, 0)
            self.assertTrue((bgdir / "gateway9999.pid").exists())
            self.assertEqual((bgdir / "gateway9999.pid").read_text(), "4321")
            # detached (start_new_session) was requested
            _, kwargs = popen.call_args
            self.assertTrue(kwargs.get("start_new_session"))

    def test_stop_kills_pid(self):
        with tempfile.TemporaryDirectory() as d:
            bgdir = Path(d)
            (bgdir / "gateway.pid").write_text("999999")
            with patch.object(kancahub, "BACKGROUND_DIR", bgdir), \
                 patch("os.kill") as kill:
                rc = kancahub._stop_background("gateway")
            self.assertEqual(rc, 0)
            kill.assert_called_once()

    def test_stop_missing_pid_is_ok(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.object(kancahub, "BACKGROUND_DIR", Path(d)):
                self.assertEqual(kancahub._stop_background("nope"), 0)


if __name__ == "__main__":
    unittest.main()
