"""
tests/test_webshare_camoufox.py — Unit tests for scripts/webshare_camoufox.py.

Pure helper tests (no browser, no network):
- parse_proxy_line parsing and formatting.
- generate_webshare_email and resolve_domain with chosen domains.
- is_login_url and is_dashboard_url truth tables.
- should_keep_polling truth table.
- append_proxies_to_file preservation and de-duplication.
- CLI argument parsing defaults and overrides.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
import sys
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.webshare_camoufox import (
    DEFAULT_OUTPUT_FILE,
    append_proxies_to_file,
    build_parser,
    generate_webshare_email,
    generate_webshare_password,
    is_dashboard_url,
    is_login_url,
    parse_proxy_line,
    pick_signup_button,
    resolve_domain,
    should_keep_polling,
)


class TestParseProxyLine(unittest.TestCase):
    """Test suite for parsing proxy line strings into structured data."""

    def test_parse_standard_at_format(self) -> None:
        raw = "myuser:mypass@192.168.1.1:8080"
        parsed = parse_proxy_line(raw)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["user"], "myuser")
        self.assertEqual(parsed["password"], "mypass")
        self.assertEqual(parsed["ip"], "192.168.1.1")
        self.assertEqual(parsed["port"], "8080")
        self.assertEqual(parsed["url"], "http://myuser:mypass@192.168.1.1:8080")

    def test_parse_http_prefixed_line(self) -> None:
        raw = "http://agaulicr:gkgjy6rwczp0@31.59.20.176:6754"
        parsed = parse_proxy_line(raw)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["user"], "agaulicr")
        self.assertEqual(parsed["password"], "gkgjy6rwczp0")
        self.assertEqual(parsed["ip"], "31.59.20.176")
        self.assertEqual(parsed["port"], "6754")
        self.assertEqual(parsed["url"], "http://agaulicr:gkgjy6rwczp0@31.59.20.176:6754")

    def test_parse_raw_colon_quad(self) -> None:
        raw = "31.59.20.176:6754:agaulicr:gkgjy6rwczp0"
        parsed = parse_proxy_line(raw)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["user"], "agaulicr")
        self.assertEqual(parsed["password"], "gkgjy6rwczp0")
        self.assertEqual(parsed["ip"], "31.59.20.176")
        self.assertEqual(parsed["port"], "6754")
        self.assertEqual(parsed["url"], "http://agaulicr:gkgjy6rwczp0@31.59.20.176:6754")

    def test_parse_invalid_lines(self) -> None:
        self.assertIsNone(parse_proxy_line(""))
        self.assertIsNone(parse_proxy_line("    "))
        self.assertIsNone(parse_proxy_line("not-a-proxy"))
        self.assertIsNone(parse_proxy_line("127.0.0.1:8080"))
        self.assertIsNone(parse_proxy_line("user:pass@127.0.0.1"))
        self.assertIsNone(parse_proxy_line(None))  # type: ignore


class TestEmailGenerator(unittest.TestCase):
    """Test suite for Webshare registration email and domain resolution."""

    def test_resolve_domain_aliases(self) -> None:
        self.assertEqual(resolve_domain("bizid"), "kancalabs.biz.id")
        self.assertEqual(resolve_domain("biz.id"), "kancalabs.biz.id")
        self.assertEqual(resolve_domain("kancalabs.biz.id"), "kancalabs.biz.id")
        self.assertEqual(resolve_domain("@kancalabs.biz.id"), "kancalabs.biz.id")
        self.assertEqual(resolve_domain("myid"), "kancalabs.my.id")
        self.assertEqual(resolve_domain("my.id"), "kancalabs.my.id")
        self.assertEqual(resolve_domain("kancalabs.my.id"), "kancalabs.my.id")
        self.assertEqual(resolve_domain("custom.org"), "custom.org")

    def test_email_generator_chosen_domain_bizid(self) -> None:
        email = generate_webshare_email("kancalabs.biz.id", rand_suffix="1234567890")
        self.assertEqual(email, "ws1234567890@kancalabs.biz.id")

    def test_email_generator_chosen_domain_myid(self) -> None:
        email = generate_webshare_email("kancalabs.my.id", rand_suffix="abcdefghij")
        self.assertEqual(email, "wsabcdefghij@kancalabs.my.id")

    def test_email_generator_default_and_random(self) -> None:
        email = generate_webshare_email()
        self.assertTrue(email.startswith("ws"))
        self.assertTrue(email.endswith("@kancalabs.biz.id"))
        self.assertGreater(len(email), 15)

    def test_generate_webshare_password(self) -> None:
        pw = generate_webshare_password()
        self.assertGreaterEqual(len(pw), 10)
        self.assertTrue(any(c in "!@#$%^&*" for c in pw))
        self.assertTrue(any(c.isdigit() for c in pw))


class TestIsLoginUrlTruthTable(unittest.TestCase):
    """Test truth table for login/register vs authenticated dashboard URL detection."""

    def test_is_login_url_truth_table(self) -> None:
        truth_table = [
            ("https://proxy.webshare.io/register", True),
            ("https://proxy.webshare.io/register/", True),
            ("https://proxy.webshare.io/login", True),
            ("https://proxy.webshare.io/signin", True),
            ("https://proxy.webshare.io/signup", True),
            ("https://dashboard.webshare.io", False),
            ("https://dashboard.webshare.io/proxy/list", False),
            ("https://proxy.webshare.io/proxy/list", False),
            ("https://dashboard.webshare.io/overview", False),
            ("", False),
        ]
        for url, expected in truth_table:
            with self.subTest(url=url):
                self.assertEqual(is_login_url(url), expected)

    def test_is_dashboard_url_truth_table(self) -> None:
        truth_table = [
            ("https://dashboard.webshare.io", True),
            ("https://dashboard.webshare.io/overview", True),
            ("https://proxy.webshare.io/proxy/list", True),
            ("https://proxy.webshare.io/dashboard", True),
            ("https://proxy.webshare.io/register", False),
            ("https://proxy.webshare.io/login", False),
            ("https://proxy.webshare.io/signin", False),
            ("", False),
        ]
        for url, expected in truth_table:
            with self.subTest(url=url):
                self.assertEqual(is_dashboard_url(url), expected)


class TestShouldKeepPollingTruthTable(unittest.TestCase):
    """Test truth table for active proxy harvesting timeout and termination conditions."""

    def test_should_keep_polling_truth_table(self) -> None:
        truth_table = [
            # (elapsed, timeout, found_count, expected)
            (0.0, 60.0, 0, True),
            (30.0, 60.0, 0, True),
            (59.9, 60.0, 0, True),
            (60.0, 60.0, 0, False),   # reached timeout
            (65.0, 60.0, 0, False),   # exceeded timeout
            (10.0, 60.0, 1, False),   # item found, stop polling
            (10.0, 60.0, 10, False),  # items found, stop polling
            (0.0, 60.0, 5, False),    # items found immediately
        ]
        for elapsed, timeout, found_count, expected in truth_table:
            with self.subTest(elapsed=elapsed, timeout=timeout, found_count=found_count):
                self.assertEqual(should_keep_polling(elapsed, timeout, found_count), expected)


class TestAppendProxiesToFile(unittest.TestCase):
    """Test appending harvested proxies to disk without clobbering or duplicating."""

    def test_append_unique_proxies_preserves_existing(self) -> None:
        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            tf.write("http://u1:p1@1.1.1.1:1000\n")
            path = Path(tf.name)

        try:
            candidates = [
                "http://u1:p1@1.1.1.1:1000",  # Duplicate: should not be added again
                "http://u2:p2@2.2.2.2:2000",  # New: should be added
                "http://u3:p3@3.3.3.3:3000",  # New: should be added
            ]
            added = append_proxies_to_file(candidates, path)
            self.assertEqual(added, 2)

            lines = [l.strip() for l in path.read_text().splitlines() if l.strip()]
            self.assertEqual(len(lines), 3)
            self.assertIn("http://u1:p1@1.1.1.1:1000", lines)
            self.assertIn("http://u2:p2@2.2.2.2:2000", lines)
            self.assertIn("http://u3:p3@3.3.3.3:3000", lines)
        finally:
            path.unlink(missing_ok=True)


class TestCLIParser(unittest.TestCase):
    """Test CLI argument parsing defaults and overrides."""

    def test_parser_defaults(self) -> None:
        parser = build_parser()
        args = parser.parse_args([])
        self.assertEqual(args.count, 1)
        self.assertFalse(args.headless)
        self.assertEqual(args.domain, "kancalabs.biz.id")
        self.assertEqual(args.out, str(DEFAULT_OUTPUT_FILE))
        self.assertIsNone(args.proxy)

    def test_parser_custom_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args([
            "--count", "3",
            "--headless",
            "--domain", "kancalabs.my.id",
            "--out", "/tmp/custom_proxies.txt",
            "--proxy", "http://127.0.0.1:8888",
        ])
        self.assertEqual(args.count, 3)
        self.assertTrue(args.headless)
        self.assertEqual(args.domain, "kancalabs.my.id")
        self.assertEqual(args.out, "/tmp/custom_proxies.txt")
        self.assertEqual(args.proxy, "http://127.0.0.1:8888")


class TestPickSignupButton(unittest.TestCase):
    """Test suite for picking the exact email registration button and rejecting Google."""

    def test_pick_exact_email_button(self) -> None:
        labels = ["Sign up with Google", "Sign Up With Email"]
        self.assertEqual(pick_signup_button(labels), "Sign Up With Email")

    def test_pick_exact_email_button_reversed_order(self) -> None:
        labels = ["Sign Up With Email", "Sign up with Google"]
        self.assertEqual(pick_signup_button(labels), "Sign Up With Email")

    def test_reject_google_only(self) -> None:
        labels = ["Sign up with Google", "Continue with Google"]
        self.assertIsNone(pick_signup_button(labels))

    def test_reject_ambiguous_sign_up(self) -> None:
        labels = ["Sign up with Google", "Sign Up"]
        self.assertIsNone(pick_signup_button(labels))

    def test_normalized_casing_and_whitespace(self) -> None:
        labels = ["  Sign up with Email  "]
        self.assertEqual(pick_signup_button(labels), "Sign up with Email")
        labels = ["Sign\nUp\nWith\nEmail"]
        self.assertEqual(pick_signup_button(labels), "Sign\nUp\nWith\nEmail")

    def test_empty_and_invalid_inputs(self) -> None:
        self.assertIsNone(pick_signup_button([]))
        self.assertIsNone(pick_signup_button([""]))
        self.assertIsNone(pick_signup_button(None))  # type: ignore
        self.assertIsNone(pick_signup_button(["Login", "Register", "Submit"]))


if __name__ == "__main__":
    unittest.main()
