"""Unit tests for farm mobile carrier rotation fallback and Gmail plus-addressing.

No live device, no external network, no Camoufox.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import kancahub  # noqa: E402


class TestMobileRotateOnce(unittest.TestCase):
    """(a) _mobile_rotate_once returns None gracefully when no device (monkeypatch)."""

    def test_no_device_returns_none_gracefully(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["mobile_rotate.py", "--rotate"],
                returncode=2,
                stdout="",
                stderr="✗ no Android device via adb. Connect the phone (USB debugging) first.\n",
            )
            res = kancahub._mobile_rotate_once(wait=1.0)
            self.assertIsNone(res)
            mock_run.assert_called_once()

    def test_subprocess_exception_returns_none_gracefully(self):
        with patch("subprocess.run", side_effect=Exception("adb connection lost")):
            res = kancahub._mobile_rotate_once(wait=1.0)
            self.assertIsNone(res)

    def test_tool_missing_returns_none(self):
        with patch.object(Path, "exists", return_value=False):
            res = kancahub._mobile_rotate_once(wait=1.0)
            self.assertIsNone(res)

    def test_successful_rotation_returns_new_ip(self):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["mobile_rotate.py", "--rotate"],
                returncode=0,
                stdout="  [mobile] IP before: 10.0.0.1\n  [mobile] IP after : 114.122.5.6\n",
                stderr="",
            )
            res = kancahub._mobile_rotate_once(wait=1.0)
            self.assertEqual(res, "114.122.5.6")

    def test_run_with_mobile_retry_block_signal_triggers_retry(self):
        call_count = 0

        def fake_run_capture(cmd, cwd=None, env=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return 1, "HTTP 403 Forbidden: access_restricted"
            return 0, "Success"

        with patch.object(kancahub, "_mobile_rotate_once", return_value="182.2.1.1") as mock_rotate, \
             patch.object(kancahub, "_run_capture", side_effect=fake_run_capture), \
             patch("time.sleep") as mock_sleep:
            code = kancahub.run_with_mobile_retry(
                ["echo", "hi"],
                mobile_rotate=True,
                wait=1.0,
            )
            self.assertEqual(code, 0)
            self.assertEqual(call_count, 2)
            self.assertEqual(mock_rotate.call_count, 2)  # initial + 1 retry rotation (capped at 2)
            mock_sleep.assert_called_once()


class TestMakePlusAddress(unittest.TestCase):
    """(b) make_plus_address('you@gmail.com','farm',3) == 'you+farm3@gmail.com'; handles domain-only and invalid input."""

    def test_standard_gmail_plus_addressing(self):
        self.assertEqual(
            kancahub.make_plus_address("you@gmail.com", "farm", 3),
            "you+farm3@gmail.com",
        )
        self.assertEqual(
            kancahub.make_plus_address("user@domain.com", "farm", 1),
            "user+farm1@domain.com",
        )
        self.assertEqual(
            kancahub.make_plus_address("custom@test.org", "test", 10),
            "custom+test10@test.org",
        )

    def test_existing_plus_address_is_replaced(self):
        self.assertEqual(
            kancahub.make_plus_address("you+old@gmail.com", "farm", 3),
            "you+farm3@gmail.com",
        )

    def test_domain_only_inputs(self):
        self.assertEqual(
            kancahub.make_plus_address("gmail.com", "farm", 3),
            "farm3@gmail.com",
        )
        self.assertEqual(
            kancahub.make_plus_address("@gmail.com", "farm", 3),
            "farm3@gmail.com",
        )
        self.assertEqual(
            kancahub.make_plus_address("kancalabs.biz.id", "worker", 2),
            "worker2@kancalabs.biz.id",
        )

    def test_invalid_inputs(self):
        self.assertEqual(kancahub.make_plus_address("", "farm", 3), "")
        self.assertEqual(kancahub.make_plus_address(None, "farm", 3), "")
        self.assertEqual(kancahub.make_plus_address("invalid", "farm", 3), "")
        self.assertEqual(kancahub.make_plus_address("notanemail@", "farm", 3), "")
        self.assertEqual(kancahub.make_plus_address(12345, "farm", 3), "")  # type: ignore


class TestFlagParsing(unittest.TestCase):
    """(c) the new flags parse on github farm, thk batch, gmail farm, autofarm."""

    def setUp(self):
        self.parser = kancahub.build_parser()

    def test_github_farm_mobile_rotate_flag(self):
        args = self.parser.parse_args(["github", "farm", "--mobile-rotate"])
        self.assertTrue(args.mobile_rotate)

        args_default = self.parser.parse_args(["github", "farm"])
        self.assertFalse(args_default.mobile_rotate)

    def test_thk_batch_mobile_rotate_flag(self):
        args = self.parser.parse_args(["thk", "batch", "--mobile-rotate"])
        self.assertTrue(args.mobile_rotate)

        args_default = self.parser.parse_args(["thk", "batch"])
        self.assertFalse(args_default.mobile_rotate)

    def test_gmail_farm_flags(self):
        args = self.parser.parse_args([
            "gmail", "farm",
            "--mobile-rotate",
            "--plus-address", "you@gmail.com",
            "--plus-prefix", "mytest",
        ])
        self.assertTrue(args.mobile_rotate)
        self.assertEqual(args.plus_address, "you@gmail.com")
        self.assertEqual(args.plus_prefix, "mytest")

        args_default = self.parser.parse_args(["gmail", "farm"])
        self.assertFalse(args_default.mobile_rotate)
        self.assertIsNone(args_default.plus_address)
        self.assertEqual(args_default.plus_prefix, "farm")

    def test_autofarm_flags(self):
        args = self.parser.parse_args([
            "autofarm", "https://example.com/signup",
            "--mobile-rotate",
            "--plus-address", "you@gmail.com",
            "--plus-prefix", "autotest",
        ])
        self.assertTrue(args.mobile_rotate)
        self.assertEqual(args.plus_address, "you@gmail.com")
        self.assertEqual(args.plus_prefix, "autotest")

        args_default = self.parser.parse_args(["autofarm", "https://example.com/signup"])
        self.assertFalse(args_default.mobile_rotate)
        self.assertIsNone(args_default.plus_address)
        self.assertEqual(args_default.plus_prefix, "farm")


if __name__ == "__main__":
    unittest.main()
