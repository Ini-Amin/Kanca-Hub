"""Tests for kancahub 9router mitm fix (dir creation + apiKey)."""
from __future__ import annotations
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, MagicMock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
import kancahub  # noqa: E402


class TestMitmFix(unittest.TestCase):
    def test_enable_precreates_dir_and_fetches_key(self):
        calls = []
        def fake_api(method, path, *, body=None, base="x"):
            calls.append((method, path, body))
            if method == "GET" and path == "/api/keys":
                return 200, '{"keys":[{"key":"sk-test"}]}'
            return 200, '{"success":true,"running":true,"pid":1}'
        p = kancahub.build_parser()
        args = p.parse_args(["9router", "mitm", "enable", "--tool", "antigravity"])
        with patch.object(kancahub, "_r9_api", side_effect=fake_api):
            rc = kancahub.cmd_9router(args)
        self.assertEqual(rc, 0)
        # it fetched a key and passed it in the POST body
        post = [c for c in calls if c[0] == "POST"][0]
        self.assertEqual(post[2].get("apiKey"), "sk-test")

    def test_dir_created(self):
        p = kancahub.build_parser()
        args = p.parse_args(["9router", "mitm", "disable"])
        with patch.object(kancahub, "_r9_api", return_value=(200, "{}")):
            kancahub.cmd_9router(args)
        self.assertTrue((Path.home() / ".9router" / "mitm").exists())


if __name__ == "__main__":
    unittest.main()
