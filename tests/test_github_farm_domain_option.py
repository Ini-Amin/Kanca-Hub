"""Unit tests for GitHub farm custom domain signup and rate-limit options.

Covers:
  - build_signup_email(): email formatting for binus, bizid, and myid domains
  - resolve_domain_and_inbox() / resolve_email_domain(): normalization
  - CLI argument parsing via build_parser(): default values and custom options
  - Rate-limit helpers: BackoffManager, is_bot_challenge, is_rate_limited
  - Launch code extraction from mail texts

Pure stdlib unittest — no live network, no browser automation.
"""

from __future__ import annotations

import inspect
from pathlib import Path
import re
import sys
import unittest

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.github_farm import (
    BackoffManager,
    build_email,
    build_parser,
    build_signup_email,
    is_bot_challenge,
    is_rate_limited,
    resolve_domain_and_inbox,
    resolve_email_domain,
    run,
    _extract_launch_code,
)


class TestCliParser(unittest.TestCase):
    """Verify CLI argument defaults and custom flag parsing."""

    def setUp(self):
        self.parser = build_parser()

    def test_default_options(self):
        args = self.parser.parse_args([])
        self.assertEqual(args.email_domain, "binus", "Default email domain must be BINUS")
        self.assertEqual(args.inbox, "binus", "Default inbox must be BINUS")
        self.assertEqual(args.delay_min, 20.0, "Default delay-min must be 20s")
        self.assertEqual(args.delay_max, 45.0, "Default delay-max must be 45s")
        self.assertEqual(args.max_accounts, 5, "Default max-accounts must be 5")
        self.assertEqual(args.index, 1)
        self.assertEqual(args.retries, 0)
        self.assertFalse(args.dry_run)

    def test_email_domain_options(self):
        args_biz = self.parser.parse_args(["--email-domain", "bizid"])
        self.assertEqual(args_biz.email_domain, "bizid")

        args_my = self.parser.parse_args(["--email-domain", "myid"])
        self.assertEqual(args_my.email_domain, "myid")

        args_binus = self.parser.parse_args(["--email-domain", "binus"])
        self.assertEqual(args_binus.email_domain, "binus")

    def test_inbox_option(self):
        args_relay = self.parser.parse_args(["--inbox", "relay"])
        self.assertEqual(args_relay.inbox, "relay")

        args_binus = self.parser.parse_args(["--inbox", "binus"])
        self.assertEqual(args_binus.inbox, "binus")

    def test_rate_limit_and_pacing_flags(self):
        args = self.parser.parse_args([
            "--delay-min", "25",
            "--delay-max", "50",
            "--max-accounts", "3",
        ])
        self.assertEqual(args.delay_min, 25.0)
        self.assertEqual(args.delay_max, 50.0)
        self.assertEqual(args.max_accounts, 3)


class TestDomainAndInboxResolution(unittest.TestCase):
    """Verify resolution and normalization between domains and inboxes."""

    def test_resolve_email_domain(self):
        self.assertEqual(resolve_email_domain("binus"), "binus.ac.id")
        self.assertEqual(resolve_email_domain("binus.ac.id"), "binus.ac.id")
        self.assertEqual(resolve_email_domain("bizid"), "kancalabs.biz.id")
        self.assertEqual(resolve_email_domain("kancalabs.biz.id"), "kancalabs.biz.id")
        self.assertEqual(resolve_email_domain("myid"), "kancalabs.my.id")
        self.assertEqual(resolve_email_domain("kancalabs.my.id"), "kancalabs.my.id")

    def test_default_binus_resolution(self):
        dom, ib = resolve_domain_and_inbox("binus", "binus")
        self.assertEqual(dom, "binus")
        self.assertEqual(ib, "binus")

    def test_custom_domain_implies_relay_inbox(self):
        dom, ib = resolve_domain_and_inbox("bizid", "binus")
        self.assertEqual(dom, "bizid")
        self.assertEqual(ib, "relay")

        dom, ib = resolve_domain_and_inbox("myid", "binus")
        self.assertEqual(dom, "myid")
        self.assertEqual(ib, "relay")

    def test_relay_inbox_defaults_domain_to_bizid(self):
        dom, ib = resolve_domain_and_inbox("binus", "relay")
        self.assertEqual(dom, "bizid")
        self.assertEqual(ib, "relay")


class TestBuildSignupEmail(unittest.TestCase):
    """Verify build_signup_email helper for all domain choices."""

    def test_binus_default(self):
        email = build_signup_email(1)
        self.assertEqual(email, "raymondi+gh1@binus.ac.id")

    def test_binus_explicit(self):
        self.assertEqual(build_signup_email(1, "binus"), "raymondi+gh1@binus.ac.id")
        self.assertEqual(build_signup_email(5, "binus"), "raymondi+gh5@binus.ac.id")
        self.assertEqual(build_signup_email(0, "binus"), "raymondi@binus.ac.id")

    def test_bizid_domain(self):
        email = build_signup_email(1, "bizid")
        self.assertTrue(email.startswith("gh"), f"Address should start with 'gh': {email}")
        self.assertTrue(email.endswith("@kancalabs.biz.id"), f"Address should end with '@kancalabs.biz.id': {email}")
        self.assertTrue(re.match(r"^gh[a-z0-9]+@kancalabs\.biz\.id$", email), f"Address pattern mismatch: {email}")

    def test_myid_domain(self):
        email = build_signup_email(1, "myid")
        self.assertTrue(email.startswith("gh"), f"Address should start with 'gh': {email}")
        self.assertTrue(email.endswith("@kancalabs.my.id"), f"Address should end with '@kancalabs.my.id': {email}")
        self.assertTrue(re.match(r"^gh[a-z0-9]+@kancalabs\.my\.id$", email), f"Address pattern mismatch: {email}")

    def test_deterministic_rand_suffix(self):
        email_biz = build_signup_email(1, "bizid", rand_suffix="test99")
        self.assertEqual(email_biz, "ghtest99@kancalabs.biz.id")

        email_my = build_signup_email(1, "myid", rand_suffix="abc123")
        self.assertEqual(email_my, "ghabc123@kancalabs.my.id")

    def test_build_email_compatibility(self):
        # build_email must remain unchanged
        self.assertEqual(build_email(1), "raymondi+gh1@binus.ac.id")
        self.assertEqual(build_email(0), "raymondi@binus.ac.id")


class TestRateLimitAndBackoff(unittest.TestCase):
    """Verify exponential backoff and rate-limit policy rules."""

    def test_backoff_progression_and_cap(self):
        bm = BackoffManager(initial=30.0, factor=2.0, cap=600.0, max_consecutive=3)
        self.assertEqual(bm.consecutive_count, 0)
        self.assertFalse(bm.should_stop)

        d1 = bm.record_rate_limit()
        self.assertEqual(d1, 30.0)
        self.assertEqual(bm.consecutive_count, 1)
        self.assertFalse(bm.should_stop)

        d2 = bm.record_rate_limit()
        self.assertEqual(d2, 60.0)
        self.assertEqual(bm.consecutive_count, 2)
        self.assertFalse(bm.should_stop)

        d3 = bm.record_rate_limit()
        self.assertEqual(d3, 120.0)
        self.assertEqual(bm.consecutive_count, 3)
        self.assertTrue(bm.should_stop, "Should stop after 3 consecutive 429/403")

    def test_backoff_cap(self):
        bm = BackoffManager(initial=200.0, factor=2.0, cap=600.0, max_consecutive=5)
        self.assertEqual(bm.record_rate_limit(), 200.0)
        self.assertEqual(bm.record_rate_limit(), 400.0)
        self.assertEqual(bm.record_rate_limit(), 600.0)
        self.assertEqual(bm.record_rate_limit(), 600.0)  # capped at 600

    def test_backoff_reset_on_success(self):
        bm = BackoffManager()
        bm.record_rate_limit()
        bm.record_rate_limit()
        self.assertEqual(bm.consecutive_count, 2)

        bm.record_success()
        self.assertEqual(bm.consecutive_count, 0)
        self.assertEqual(bm.current_delay, 30.0)
        self.assertFalse(bm.should_stop)

    def test_challenge_detection(self):
        self.assertTrue(is_bot_challenge("captcha_after_email", "captcha:Arkose / FunCaptcha"))
        self.assertTrue(is_bot_challenge("access_restricted", "network/IP block: 'Access is temporarily restricted'"))
        self.assertTrue(is_bot_challenge("cloudflare_challenge", "cf-chl"))
        self.assertTrue(is_bot_challenge(None, "datadome challenge"))
        self.assertFalse(is_bot_challenge("start", None))
        self.assertFalse(is_bot_challenge("email_filled", None))
        self.assertFalse(is_bot_challenge("account_created", None))

    def test_rate_limited_detection(self):
        self.assertTrue(is_rate_limited("http_429", "HTTP 429"))
        self.assertTrue(is_rate_limited("http_403", "HTTP 403"))
        self.assertTrue(is_rate_limited(None, "rate limit exceeded"))
        self.assertTrue(is_rate_limited(None, "too many requests"))
        self.assertFalse(is_rate_limited("start", None))
        self.assertFalse(is_rate_limited("account_created", None))


class TestLaunchCodeExtraction(unittest.TestCase):
    """Verify launch code extraction from email texts."""

    def test_extract_launch_code_various_formats(self):
        self.assertEqual(
            _extract_launch_code(["Here is your launch code: 12345678"]),
            "12345678"
        )
        self.assertEqual(
            _extract_launch_code(["12345678 is your launch code to continue"]),
            "12345678"
        )
        self.assertEqual(
            _extract_launch_code(["Your GitHub launch code is 87654321"]),
            "87654321"
        )
        self.assertEqual(
            _extract_launch_code(["GitHub verification code: 99887766"]),
            "99887766"
        )
        self.assertIsNone(_extract_launch_code(["No code here, just random text"]))


class TestRunSignatureDefaults(unittest.TestCase):
    """Verify run() retains expected keyword arguments and defaults."""

    def test_run_signature_parameters(self):
        sig = inspect.signature(run)
        self.assertIn("retries", sig.parameters)
        self.assertEqual(sig.parameters["retries"].default, 0)

        self.assertIn("email_domain", sig.parameters)
        self.assertEqual(sig.parameters["email_domain"].default, "binus")

        self.assertIn("inbox", sig.parameters)
        self.assertEqual(sig.parameters["inbox"].default, "binus")

        self.assertIn("delay_min", sig.parameters)
        self.assertEqual(sig.parameters["delay_min"].default, 20.0)

        self.assertIn("delay_max", sig.parameters)
        self.assertEqual(sig.parameters["delay_max"].default, 45.0)

        self.assertIn("max_accounts", sig.parameters)
        self.assertEqual(sig.parameters["max_accounts"].default, 5)


if __name__ == "__main__":
    unittest.main()
