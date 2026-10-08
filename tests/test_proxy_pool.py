"""Tests for scripts/proxy_pool.py (pure, no network)."""
from __future__ import annotations

import sys
from pathlib import Path
import unittest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import proxy_pool as P  # noqa: E402


class TestParse(unittest.TestCase):
    def test_host_port(self):
        self.assertEqual(P.parse_proxy_line("1.2.3.4:8080"), "http://1.2.3.4:8080")

    def test_user_pass_at(self):
        self.assertEqual(P.parse_proxy_line("u:p@1.2.3.4:8080"), "http://u:p@1.2.3.4:8080")

    def test_host_port_user_pass(self):
        self.assertEqual(P.parse_proxy_line("1.2.3.4:8080:u:p"), "http://u:p@1.2.3.4:8080")

    def test_scheme_preserved(self):
        self.assertEqual(P.parse_proxy_line("socks5://1.2.3.4:1080"), "socks5://1.2.3.4:1080")

    def test_blank_and_comment(self):
        self.assertIsNone(P.parse_proxy_line(""))
        self.assertIsNone(P.parse_proxy_line("# note"))


class TestTemplate(unittest.TestCase):
    def test_expand_session_rotates(self):
        a = P.expand_template("http://u-session-{session}:p@gw:22225", 0)
        b = P.expand_template("http://u-session-{session}:p@gw:22225", 1)
        self.assertIn("-session-", a)
        self.assertNotEqual(a, b)

    def test_expand_country(self):
        out = P.expand_template("http://u-country-{country}:p@gw:1", 0, country="us")
        self.assertIn("-country-us", out)


class TestPool(unittest.TestCase):
    def test_round_robin(self):
        pool = P.ProxyPool(proxy_list=["h:1", "h:2"])
        self.assertEqual(pool.assign(0), "http://h:1")
        self.assertEqual(pool.assign(1), "http://h:2")
        self.assertEqual(pool.assign(2), "http://h:1")

    def test_template_new_session_per_account(self):
        pool = P.ProxyPool(proxy_template="http://u-{session}:p@gw:22225")
        self.assertNotEqual(pool.assign(0), pool.assign(1))

    def test_direct_when_empty(self):
        self.assertEqual(P.ProxyPool().assign(0), "")

    def test_rotate_changes(self):
        pool = P.ProxyPool(proxy_list=["h:1", "h:2"])
        first = pool.assign(0)
        self.assertNotEqual(pool.rotate(first), first)


if __name__ == "__main__":
    unittest.main()