"""Pure tests for scripts/mailboxes.py (no network)."""
from __future__ import annotations

import sys
from pathlib import Path
import unittest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import mailboxes as M  # noqa: E402


class TestCodeFrom(unittest.TestCase):
    def test_github_dashed(self):
        self.assertEqual(M._code_from("code 1234-5678"), "12345678")

    def test_plain_digits(self):
        self.assertEqual(M._code_from("Your code is 998877"), "998877")

    def test_html(self):
        self.assertEqual(M._code_from("<b>4321</b>"), "4321")

    def test_none(self):
        self.assertIsNone(M._code_from("no digits"))
        self.assertIsNone(M._code_from(""))


class TestStaticMailbox(unittest.TestCase):
    def test_address_and_empty_read(self):
        mb = M._static("kancalabs.my.id", "usr")
        self.assertEqual(mb.address, "usr@kancalabs.my.id")
        self.assertEqual(mb.read(), [])


class TestMailboxCode(unittest.TestCase):
    def test_code_prefers_vendor_and_skips_excludes(self):
        mb = M.Mailbox("x@y", lambda: [
            {"subject": "unrelated", "body": "111111"},
            {"subject": "Acme verify", "body": "222222"},
        ])
        self.assertEqual(mb.code(vendor="acme", timeout=1, interval=0.01), "222222")
        self.assertEqual(mb.code(vendor="acme", timeout=1, interval=0.01, exclude={"222222"}), "111111")

    def test_timeout_returns_none(self):
        mb = M.Mailbox("x@y", lambda: [])
        self.assertIsNone(mb.code(timeout=0.05, interval=0.01))


class TestOpenMailboxFallback(unittest.TestCase):
    def test_unknown_provider_static(self):
        mb = M.open_mailbox("nope", domain="kancalabs.my.id", log=lambda *a: None)
        self.assertTrue(mb.address.endswith("@kancalabs.my.id"))


if __name__ == "__main__":
    unittest.main()