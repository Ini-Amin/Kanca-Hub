"""Unit tests for scripts/github_to_kiro.py (stdlib unittest, no live network)."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.github_to_kiro import (
    BackoffManager,
    build_exchange_request,
    build_kiro_auth_url,
    build_sqlite_connection_record,
    derive_cli_token,
    extract_code_from_redirect_url,
    extract_email_from_jwt,
    generate_pkce,
    get_account_delay,
    get_action_delay,
    load_github_accounts,
    DEFAULT_DELAY_MIN,
    DEFAULT_DELAY_MAX,
    DEFAULT_MAX_ACCOUNTS,
    DEFAULT_PORT,
)


class TestAccountLoading(unittest.TestCase):
    """Test pure account loading logic for github_accounts.json."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_json(self, data: object) -> Path:
        p = Path(self.temp_dir.name) / f"accounts_{os.urandom(4).hex()}.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_load_wrapped_accounts_dict(self) -> None:
        data = {
            "accounts": [
                {"username": "user1", "email": "u1@test.org", "password": "pass1"},
                {"login": "user2", "password": "pass2"},
            ],
            "updated": "2026-10-06T00:00:00Z",
        }
        f = self._write_json(data)
        accounts = load_github_accounts(f)
        self.assertEqual(len(accounts), 2)
        self.assertEqual(accounts[0]["username"], "user1")
        self.assertEqual(accounts[1]["login"], "user2")

    def test_load_raw_list(self) -> None:
        data = [
            {"email": "solo@domain.com", "password": "secret_pass"},
        ]
        f = self._write_json(data)
        accounts = load_github_accounts(f)
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["email"], "solo@domain.com")

    def test_filter_invalid_accounts(self) -> None:
        data = {
            "accounts": [
                {"username": "valid_user", "password": "valid_password"},
                {"username": "missing_password"},
                {"password": "missing_login_identifier"},
                {"username": "   ", "password": "pass"},  # whitespace login
                "not_a_dict",
                None,
            ]
        }
        f = self._write_json(data)
        accounts = load_github_accounts(f)
        self.assertEqual(len(accounts), 1)
        self.assertEqual(accounts[0]["username"], "valid_user")

    def test_missing_and_corrupt_files(self) -> None:
        missing = Path(self.temp_dir.name) / "non_existent.json"
        self.assertEqual(load_github_accounts(missing), [])

        corrupt = Path(self.temp_dir.name) / "corrupt.json"
        corrupt.write_text("invalid json content {{{", encoding="utf-8")
        self.assertEqual(load_github_accounts(corrupt), [])


class TestPKCEAndAuthUrl(unittest.TestCase):
    """Test PKCE generation, URL construction, and redirect code extraction."""

    def test_generate_pkce(self) -> None:
        verifier, challenge, state = generate_pkce(num_bytes=32)

        # Base64url unpadded characters only
        for s in (verifier, challenge, state):
            self.assertRegex(s, r"^[A-Za-z0-9_-]+$")
            self.assertNotIn("=", s)

        # Verify challenge is SHA256 of verifier
        expected_digest = hashlib.sha256(verifier.encode("ascii")).digest()
        expected_challenge = base64.urlsafe_b64encode(expected_digest).rstrip(b"=").decode("ascii")
        self.assertEqual(challenge, expected_challenge)

    def test_build_kiro_auth_url(self) -> None:
        challenge = "test_challenge_abc"
        state = "test_state_123"
        url = build_kiro_auth_url(challenge, state)

        self.assertTrue(url.startswith("https://prod.us-east-1.auth.desktop.kiro.dev/login?"))
        self.assertIn("idp=Github", url)
        self.assertIn("redirect_uri=kiro%3A%2F%2Fkiro.kiroAgent%2Fauthenticate-success", url)
        self.assertIn(f"code_challenge={challenge}", url)
        self.assertIn("code_challenge_method=S256", url)
        self.assertIn(f"state={state}", url)
        self.assertIn("prompt=select_account", url)

    def test_extract_code_from_redirect_url_success(self) -> None:
        redirect = "kiro://kiro.kiroAgent/authenticate-success?code=launch_code_999&state=expected_state_abc"
        code, err = extract_code_from_redirect_url(redirect, expected_state="expected_state_abc")
        self.assertEqual(code, "launch_code_999")
        self.assertIsNone(err)

    def test_extract_code_from_redirect_url_state_mismatch(self) -> None:
        redirect = "kiro://kiro.kiroAgent/authenticate-success?code=launch_code_999&state=wrong_state"
        code, err = extract_code_from_redirect_url(redirect, expected_state="expected_state_abc")
        self.assertIsNone(code)
        self.assertIn("State mismatch", str(err))

    def test_extract_code_from_redirect_url_error_response(self) -> None:
        redirect = "kiro://kiro.kiroAgent/authenticate-success?error=access_denied&error_description=User+cancelled"
        code, err = extract_code_from_redirect_url(redirect)
        self.assertIsNone(code)
        self.assertIn("access_denied: User cancelled", str(err))

    def test_extract_code_from_redirect_url_invalid_scheme(self) -> None:
        redirect = "https://example.com/callback?code=abc"
        code, err = extract_code_from_redirect_url(redirect)
        self.assertIsNone(code)
        self.assertIn("Invalid URL scheme", str(err))


class TestBackoffManagerTruthTable(unittest.TestCase):
    """Test exponential backoff progression and truth table for HTTP 429/403."""

    def test_backoff_truth_table(self) -> None:
        mgr = BackoffManager(base_delay=30.0, max_delay=600.0, max_consecutive=3)

        # Initial state
        self.assertEqual(mgr.consecutive_rate_limits, 0)
        self.assertFalse(mgr.should_stop())

        # 1st 429/403: delay = 30.0 (30 * 2^0), stop = False
        d1 = mgr.record_rate_limit()
        self.assertEqual(d1, 30.0)
        self.assertEqual(mgr.consecutive_rate_limits, 1)
        self.assertFalse(mgr.should_stop())

        # 2nd 429/403: delay = 60.0 (30 * 2^1), stop = False
        d2 = mgr.record_rate_limit()
        self.assertEqual(d2, 60.0)
        self.assertEqual(mgr.consecutive_rate_limits, 2)
        self.assertFalse(mgr.should_stop())

        # 3rd 429/403: delay = 120.0 (30 * 2^2), stop = True (STOP after 3 consecutive!)
        d3 = mgr.record_rate_limit()
        self.assertEqual(d3, 120.0)
        self.assertEqual(mgr.consecutive_rate_limits, 3)
        self.assertTrue(mgr.should_stop())

        # 4th error past cap: delay = 0.0, stop = True
        d4 = mgr.record_rate_limit()
        self.assertEqual(d4, 0.0)
        self.assertTrue(mgr.should_stop())

    def test_backoff_cap_at_max_delay(self) -> None:
        # Small base to test cap quickly
        mgr = BackoffManager(base_delay=100.0, max_delay=300.0, max_consecutive=10)
        mgr.record_rate_limit()  # 100
        mgr.record_rate_limit()  # 200
        d3 = mgr.record_rate_limit()  # 400 capped to 300
        self.assertEqual(d3, 300.0)

    def test_reset_on_success(self) -> None:
        mgr = BackoffManager(base_delay=30.0, max_delay=600.0, max_consecutive=3)
        mgr.record_rate_limit()
        mgr.record_rate_limit()
        self.assertEqual(mgr.consecutive_rate_limits, 2)

        mgr.record_success()
        self.assertEqual(mgr.consecutive_rate_limits, 0)
        self.assertFalse(mgr.should_stop())

        # Subsequent error starts back at base delay
        d_next = mgr.record_rate_limit()
        self.assertEqual(d_next, 30.0)

    def test_delay_helpers_bounds(self) -> None:
        for _ in range(20):
            ac_delay = get_account_delay(20.0, 45.0)
            self.assertGreaterEqual(ac_delay, 20.0)
            self.assertLessEqual(ac_delay, 45.0)

            act_delay = get_action_delay(1.5, 4.0)
            self.assertGreaterEqual(act_delay, 1.5)
            self.assertLessEqual(act_delay, 4.0)


class TestRequestShapeBuilders(unittest.TestCase):
    """Test 9Router exchange payload, token derivation, and DB records."""

    def test_build_exchange_request(self) -> None:
        req = build_exchange_request(code="code_xyz", code_verifier="verifier_abc")
        self.assertEqual(
            req,
            {
                "code": "code_xyz",
                "codeVerifier": "verifier_abc",
                "provider": "github",
            },
        )

    def test_derive_cli_token_from_env(self) -> None:
        with patch.dict(os.environ, {"R9_TOKEN": "custom_env_token_123"}):
            tok = derive_cli_token()
            self.assertEqual(tok, "custom_env_token_123")

    def test_derive_cli_token_from_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            r9_dir = Path(td)
            mid_file = r9_dir / "machine-id"
            mid_file.write_text("mach-12345", encoding="utf-8")
            auth_dir = r9_dir / "auth"
            auth_dir.mkdir()
            sec_file = auth_dir / "cli-secret"
            sec_file.write_text("secret-67890", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                tok = derive_cli_token(r9_dir=r9_dir)
                expected = hashlib.sha256("mach-123459r-cli-authsecret-67890".encode("utf-8")).hexdigest()[:16]
                self.assertEqual(tok, expected)

    def test_derive_cli_token_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch.dict(os.environ, {}, clear=True):
                tok = derive_cli_token(r9_dir=Path(td))
                self.assertEqual(tok, "")

    def test_extract_email_from_jwt(self) -> None:
        # Mock payload: {"email": "oauth_user@example.com", "sub": "sub123"}
        payload_dict = {"email": "oauth_user@example.com", "sub": "sub123"}
        payload_bytes = json.dumps(payload_dict).encode("utf-8")
        payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")
        fake_jwt = f"header.{payload_b64}.signature"

        email = extract_email_from_jwt(fake_jwt)
        self.assertEqual(email, "oauth_user@example.com")

        # Invalid token formats
        self.assertIsNone(extract_email_from_jwt("invalid_jwt"))
        self.assertIsNone(extract_email_from_jwt("header.corrupt_payload.sig"))

    def test_build_sqlite_connection_record(self) -> None:
        tokens = {
            "accessToken": "tok_access_123",
            "refreshToken": "tok_refresh_456",
            "profileArn": "arn:aws:profile:123",
            "expiresIn": 3600,
        }
        rec = build_sqlite_connection_record(tokens, email="tester@kancalabs.biz.id")

        self.assertEqual(rec["provider"], "kiro")
        self.assertEqual(rec["authType"], "oauth")
        self.assertEqual(rec["email"], "tester@kancalabs.biz.id")
        self.assertEqual(rec["priority"], 1)
        self.assertEqual(rec["isActive"], 1)

        data = json.loads(rec["data"])
        self.assertEqual(data["accessToken"], "tok_access_123")
        self.assertEqual(data["refreshToken"], "tok_refresh_456")
        self.assertEqual(data["providerSpecificData"]["authMethod"], "github")
        self.assertEqual(data["providerSpecificData"]["provider"], "Github")
        self.assertEqual(data["providerSpecificData"]["profileArn"], "arn:aws:profile:123")


class TestCLIParserDefaults(unittest.TestCase):
    """Test CLI argument parser options and defaults."""

    def test_defaults(self) -> None:
        self.assertEqual(DEFAULT_MAX_ACCOUNTS, 3)
        self.assertEqual(DEFAULT_DELAY_MIN, 20.0)
        self.assertEqual(DEFAULT_DELAY_MAX, 45.0)
        self.assertEqual(DEFAULT_PORT, 20128)


if __name__ == "__main__":
    unittest.main()
