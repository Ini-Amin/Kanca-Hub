"""Tests for kancahub 9router mitm + gmail env-based plus-address."""
from __future__ import annotations
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import kancahub  # noqa: E402


class TestR9Mitm(unittest.TestCase):
    def test_cli_token_derivation(self):
        with patch("pathlib.Path.read_text", side_effect=["mid123", "sec456"]):
            tok = kancahub._r9_cli_token()
        import hashlib
        self.assertEqual(tok, hashlib.sha256(b"mid1239r-cli-authsec456").hexdigest()[:16])

    def test_r9_api_builds_request(self):
        with patch.object(kancahub, "_r9_cli_token", return_value="tok"), \
             patch("urllib.request.urlopen") as uo:
            uo.return_value.__enter__.return_value.status = 200
            uo.return_value.__enter__.return_value.read.return_value = b'{"ok":true}'
            code, body = kancahub._r9_api("GET", "/api/cli-tools/antigravity-mitm")
        self.assertEqual(code, 200)
        self.assertIn("ok", body)

    def test_mitm_cmd_parses(self):
        p = kancahub.build_parser()
        a = p.parse_args(["9router", "mitm", "enable", "--tool", "kiro", "--sudo-password", "x"])
        self.assertEqual(a.r9_cmd, "mitm")
        self.assertEqual(a.action, "enable")
        self.assertEqual(a.tool, "kiro")


class TestGmailEnvPlusAddress(unittest.TestCase):
    def test_reads_env_base_address(self):
        with patch.object(kancahub, "_env_get", side_effect=lambda k, f: "nowhanderhoy@gmail.com" if k == "GMAIL_FARM_ADDRESS" else None):
            # simulate the farm branch resolution
            addr = kancahub._env_get("GMAIL_FARM_ADDRESS", kancahub.ENV_FILE)
            pfx = kancahub._env_get("GMAIL_FARM_PREFIX", kancahub.ENV_FILE) or "farm"
        self.assertEqual(kancahub.make_plus_address(addr, pfx, 2), "nowhanderhoy+farm2@gmail.com")


if __name__ == "__main__":
    unittest.main()
