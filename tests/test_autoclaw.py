"""Unit tests for AutoClaw client and CLI subcommand."""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import autoclaw_client as ac
import kancahub


class TestAutoClawClient(unittest.TestCase):
    def test_make_headers_sign(self):
        h = ac.make_headers()
        self.assertIn("X-Auth-Sign", h)
        self.assertIn("X-Auth-TimeStamp", h)
        self.assertEqual(h["X-Auth-Appid"], ac.APP_ID)
        self.assertEqual(h["X-Product"], "autoclaw")
        # Verify MD5 signature formula
        expected_sign = hashlib.md5(f"{ac.APP_ID}&{h['X-Auth-TimeStamp']}&{ac.APP_KEY}".encode()).hexdigest()
        self.assertEqual(h["X-Auth-Sign"], expected_sign)

    def test_make_headers_token(self):
        h1 = ac.make_headers(token="secret_token")
        self.assertEqual(h1["Authorization"], "Bearer secret_token")
        h2 = ac.make_headers(token="Bearer already_bearer")
        self.assertEqual(h2["Authorization"], "Bearer already_bearer")

    def test_oauth_url_payload(self):
        client = ac.AutoClawClient()
        res = client.get_oauth_url(captcha_verify_param="mock_proof")
        self.assertIn("code", res)
    def test_account_persistence(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td) / "test_accounts.jsonl"
            self.assertEqual(ac.load_accounts(tmp_path), [])
            self.assertIsNone(ac.latest_token(tmp_path))

            record = {
                "user_id": "usr_test123",
                "user_name": "Test User",
                "phone": "13812345678",
                "access_token": "token_abc_123",
                "refresh_token": "refresh_xyz_456",
                "device_id": "dev_test",
                "created_at": "2026-10-14 12:00:00",
            }
            ac.save_account(record, path=tmp_path)

            loaded = ac.load_accounts(tmp_path)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["user_id"], "usr_test123")
            self.assertEqual(ac.latest_token(tmp_path), "token_abc_123")

    def test_interactive_claim_structure(self):
        client = ac.AutoClawClient()
        self.assertTrue(hasattr(client, "interactive_claim"))
        self.assertTrue(callable(client.interactive_claim))


class TestAutoClawCLI(unittest.TestCase):
    def setUp(self):
        self.parser = kancahub.build_parser()

    def test_parser_autoclaw_subcommands(self):
        # test subcommands are parsed
        args_status = self.parser.parse_args(["autoclaw", "status"])
        self.assertEqual(args_status.group, "autoclaw")
        self.assertEqual(args_status.autoclaw_cmd, "status")

        args_login_listen = self.parser.parse_args(["autoclaw", "login", "--listen", "--bind", "INV123"])
        self.assertEqual(args_login_listen.group, "autoclaw")
        self.assertEqual(args_login_listen.autoclaw_cmd, "login")
        self.assertTrue(args_login_listen.listen)
        self.assertEqual(args_login_listen.bind, "INV123")
        self.assertEqual(args_login_listen.provider, "zai")

        args_login_code = self.parser.parse_args(["autoclaw", "login", "--code", "CODE123", "--state", "STATE456"])
        self.assertEqual(args_login_code.code, "CODE123")
        self.assertEqual(args_login_code.state, "STATE456")
        args_url = self.parser.parse_args(["autoclaw", "oauth-url", "--provider", "google", "--verify-param", "proof_xyz"])
        self.assertEqual(args_url.provider, "google")
        self.assertEqual(args_url.verify_param, "proof_xyz")

        args_bind = self.parser.parse_args(["autoclaw", "bind", "INV999", "--token", "tok_override"])
        self.assertEqual(args_bind.invite_code, "INV999")
        self.assertEqual(args_bind.token, "tok_override")
        args_claim = self.parser.parse_args(["autoclaw", "claim", "--bind", "INV555", "--port", "19999"])
        self.assertEqual(args_claim.group, "autoclaw")
        self.assertEqual(args_claim.autoclaw_cmd, "claim")
        self.assertEqual(args_claim.bind, "INV555")
        self.assertEqual(args_claim.port, 19999)

    def test_bare_login_parses_with_defaults(self):
        args = self.parser.parse_args(["autoclaw", "login"])
        self.assertEqual(args.autoclaw_cmd, "login")
        self.assertFalse(args.listen)
        self.assertIsNone(args.code)
        self.assertIsNone(args.state)
        self.assertIsNone(args.verify_param)

    def test_busy_port_message(self):
        import socket
        from http.server import HTTPServer
        blocker = HTTPServer(("127.0.0.1", 0), lambda *a, **k: None)
        busy_port = blocker.server_address[1]
        try:
            client = ac.AutoClawClient()
            res = client.interactive_claim(port=busy_port, timeout=1.0, open_browser=False)
            self.assertEqual(res.get("code"), 1)
            self.assertIn(str(busy_port), res.get("msg", ""))
            self.assertIn("--port", res.get("msg", ""))
        finally:
            blocker.server_close()

if __name__ == "__main__":
    unittest.main()
