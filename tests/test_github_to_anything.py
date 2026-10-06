"""Unit tests for scripts/github_to_anything.py."""

from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.github_to_anything import (
    BackoffManager,
    build_parser,
    build_temp_email,
    detect_social_target,
    extract_otp_code,
    extract_verification_link,
    load_github_accounts,
    main,
    resolve_domain,
)


class TestAccountLoading(unittest.TestCase):
    """Test loading GitHub accounts from JSON files (wrapped dict and raw list)."""

    def test_load_accounts_wrapped_dict(self) -> None:
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            f.write(json.dumps({
                "accounts": [
                    {"username": "testuser", "password": "pass123", "email": "test@example.com"},
                    {"login": "seconduser", "password": "pass456"},
                ]
            }))
            f_path = f.name

        try:
            accs = load_github_accounts(f_path)
            self.assertEqual(len(accs), 2)
            self.assertEqual(accs[0]["username"], "testuser")
            self.assertEqual(accs[1]["login"], "seconduser")
        finally:
            Path(f_path).unlink(missing_ok=True)

    def test_load_accounts_raw_list(self) -> None:
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            f.write(json.dumps([
                {"username": "rawuser", "password": "rawpass"},
            ]))
            f_path = f.name

        try:
            accs = load_github_accounts(f_path)
            self.assertEqual(len(accs), 1)
            self.assertEqual(accs[0]["username"], "rawuser")
        finally:
            Path(f_path).unlink(missing_ok=True)

    def test_load_accounts_filters_invalid_entries(self) -> None:
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            f.write(json.dumps({
                "accounts": [
                    {"username": "nopass"},  # missing password
                    {"password": "nolu"},    # missing login
                    {"username": "", "password": "p"},
                    {"username": "valid", "password": "validpass"},
                ]
            }))
            f_path = f.name

        try:
            accs = load_github_accounts(f_path)
            self.assertEqual(len(accs), 1)
            self.assertEqual(accs[0]["username"], "valid")
        finally:
            Path(f_path).unlink(missing_ok=True)

    def test_load_accounts_missing_or_corrupt_file(self) -> None:
        self.assertEqual(load_github_accounts("/path/that/does/not/exist.json"), [])

        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            f.write("NOT_JSON")
            f_path = f.name

        try:
            self.assertEqual(load_github_accounts(f_path), [])
        finally:
            Path(f_path).unlink(missing_ok=True)


class TestSocialTargetDetector(unittest.TestCase):
    """Test pure function detect_social_target -> 'github' | 'google' | 'email'."""

    def test_detects_github_over_others(self) -> None:
        labels = ["Continue with GitHub", "Continue with Google", "Sign up with email"]
        self.assertEqual(detect_social_target(labels), "github")

    def test_detects_github_case_insensitive(self) -> None:
        labels = ["SIGN IN WITH GITHUB", "Log in"]
        self.assertEqual(detect_social_target(labels), "github")

        labels2 = ["github-oauth-button", "Submit"]
        self.assertEqual(detect_social_target(labels2), "github")

    def test_detects_google_when_no_github(self) -> None:
        labels = ["Continue with Google", "Sign up with email"]
        self.assertEqual(detect_social_target(labels), "google")

        labels2 = ["Sign in with Google", "Cancel"]
        self.assertEqual(detect_social_target(labels2), "google")

    def test_detects_email_when_email_keywords_present(self) -> None:
        labels = ["Sign up with email", "Log in with password"]
        self.assertEqual(detect_social_target(labels), "email")

        labels2 = ["Create an account", "Register"]
        self.assertEqual(detect_social_target(labels2), "email")

    def test_fallback_to_email_for_unknown_labels(self) -> None:
        labels = ["Submit", "Next", "Continue"]
        self.assertEqual(detect_social_target(labels), "email")
        self.assertEqual(detect_social_target([]), "email")


class TestEmailGenerator(unittest.TestCase):
    """Test pure function build_temp_email and domain resolution."""

    def test_resolve_domain(self) -> None:
        self.assertEqual(resolve_domain("bizid"), "kancalabs.biz.id")
        self.assertEqual(resolve_domain("biz.id"), "kancalabs.biz.id")
        self.assertEqual(resolve_domain("myid"), "kancalabs.my.id")
        self.assertEqual(resolve_domain("kancalabs.my.id"), "kancalabs.my.id")

    def test_build_temp_email_bizid(self) -> None:
        email = build_temp_email("bizid")
        self.assertTrue(email.startswith("usr_"))
        self.assertTrue(email.endswith("@kancalabs.biz.id"))

    def test_build_temp_email_myid(self) -> None:
        email = build_temp_email("myid")
        self.assertTrue(email.startswith("usr_"))
        self.assertTrue(email.endswith("@kancalabs.my.id"))

    def test_build_temp_email_deterministic_suffix(self) -> None:
        email = build_temp_email("bizid", rand_suffix="test999")
        self.assertEqual(email, "usr_test999@kancalabs.biz.id")

        email_my = build_temp_email("myid", rand_suffix="abc1234")
        self.assertEqual(email_my, "usr_abc1234@kancalabs.my.id")


class TestCLIAndHonestFailure(unittest.TestCase):
    """Test CLI argument parsing and honest failure when no accounts exist."""

    def test_cli_positional_target(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["https://console.tiarina.cloud/login"])
        self.assertEqual(args.target, "https://console.tiarina.cloud/login")
        self.assertIsNone(args.target_opt)

    def test_cli_flag_target(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["--target", "https://www.codebuddy.ai/login"])
        self.assertEqual(args.target_opt, "https://www.codebuddy.ai/login")

    def test_cli_options_parsing(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "https://example.com/login",
            "--account", "gh_user@example.com",
            "--domain", "myid",
            "--proxy", "http://127.0.0.1:8888",
            "--headless",
            "--inject-9router", "http://127.0.0.1:20128/v1",
            "--max-accounts", "2",
        ])
        self.assertEqual(args.target, "https://example.com/login")
        self.assertEqual(args.account, "gh_user@example.com")
        self.assertEqual(args.domain, "myid")
        self.assertEqual(args.proxy, "http://127.0.0.1:8888")
        self.assertTrue(args.headless)
        self.assertEqual(args.inject_9router, "http://127.0.0.1:20128/v1")
        self.assertEqual(args.max_accounts, 2)

    def test_honest_failure_when_no_accounts(self) -> None:
        # Create an empty accounts file
        with tempfile.NamedTemporaryFile("w+", suffix=".json", delete=False) as f:
            f.write(json.dumps({"accounts": []}))
            f_path = f.name

        stderr_buf = io.StringIO()
        try:
            with patch("sys.stderr", stderr_buf):
                rc = main(["https://console.tiarina.cloud/login", "--accounts-file", f_path])
            self.assertEqual(rc, 2)
            self.assertIn("no GitHub account yet — run kancahub github farm first", stderr_buf.getvalue())
        finally:
            Path(f_path).unlink(missing_ok=True)

    def test_missing_target_url_returns_one(self) -> None:
        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            rc = main([])
        self.assertEqual(rc, 1)
        self.assertIn("Error: Target URL is required", stderr_buf.getvalue())


class TestBackoffManager(unittest.TestCase):
    """Test exponential backoff truth table and rate policy."""

    def test_backoff_progression(self) -> None:
        bm = BackoffManager(initial=30.0, factor=2.0, cap=600.0, max_consecutive=3)
        self.assertEqual(bm.current_delay, 30.0)
        self.assertFalse(bm.should_stop())

        # Error 1
        d1 = bm.record_error()
        self.assertEqual(d1, 30.0)
        self.assertEqual(bm.current_delay, 60.0)
        self.assertFalse(bm.should_stop())

        # Error 2
        d2 = bm.record_error()
        self.assertEqual(d2, 60.0)
        self.assertEqual(bm.current_delay, 120.0)
        self.assertFalse(bm.should_stop())

        # Error 3 -> should stop!
        d3 = bm.record_error()
        self.assertEqual(d3, 120.0)
        self.assertTrue(bm.should_stop())

    def test_backoff_resets_on_success(self) -> None:
        bm = BackoffManager(initial=30.0, factor=2.0, cap=600.0, max_consecutive=3)
        bm.record_error()
        bm.record_error()
        self.assertEqual(bm.consecutive_count, 2)

        bm.record_success()
        self.assertEqual(bm.consecutive_count, 0)
        self.assertEqual(bm.current_delay, 30.0)
        self.assertFalse(bm.should_stop())


class TestExtractLinksAndOTP(unittest.TestCase):
    """Test pure extraction helpers for verification links and OTP codes."""

    def test_extract_verification_link(self) -> None:
        text = "Welcome to Kanca! Please verify your account: https://console.tiarina.cloud/verify?token=abc123xyz"
        link = extract_verification_link(text, host="tiarina.cloud")
        self.assertEqual(link, "https://console.tiarina.cloud/verify?token=abc123xyz")

    def test_extract_otp_code(self) -> None:
        text1 = "Your verification code is 849201 to complete signup."
        self.assertEqual(extract_otp_code(text1), "849201")

        text2 = "GitHub launch code: 12345678"
        self.assertEqual(extract_otp_code(text2), "12345678")


if __name__ == "__main__":
    unittest.main()
