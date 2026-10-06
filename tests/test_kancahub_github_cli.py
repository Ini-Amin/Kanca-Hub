"""Unit tests for kancahub github CLI subcommands ('farm', 'verify', 'check')."""

from __future__ import annotations

import argparse
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.kancahub import (
    build_github_parser,
    build_parser,
    cmd_github,
    map_github_farm_args,
)


class TestKancahubGithubCLI(unittest.TestCase):
    """Test CLI parser construction and argument mapping for 'kancahub github'."""

    def test_subcommands_exist_in_standalone_parser(self) -> None:
        parser = build_github_parser()
        # Verify parser has subparser choices for farm, verify, check
        subparsers_action = next(
            (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)),
            None,
        )
        self.assertIsNotNone(subparsers_action, "Subparsers action should exist")
        choices = subparsers_action.choices
        self.assertIn("farm", choices)
        self.assertIn("verify", choices)
        self.assertIn("check", choices)

    def test_subcommands_exist_in_full_kancahub_parser(self) -> None:
        kp = build_parser()
        # Verify kancahub github farm parses
        args_farm = kp.parse_args(["github", "farm", "--domain", "bizid"])
        self.assertEqual(args_farm.group, "github")
        self.assertEqual(args_farm.github_cmd, "farm")
        self.assertEqual(args_farm.domain, "bizid")

        # Verify kancahub github verify parses
        args_verify = kp.parse_args(["github", "verify", "--from-mail"])
        self.assertEqual(args_verify.group, "github")
        self.assertEqual(args_verify.github_cmd, "verify")
        self.assertTrue(args_verify.from_mail)

        # Verify kancahub github check parses
        args_check = kp.parse_args(["github", "check", "--domain", "myid"])
        self.assertEqual(args_check.group, "github")
        self.assertEqual(args_check.github_cmd, "check")
        self.assertEqual(args_check.domain, "myid")

    def test_farm_domain_maps_to_email_domain(self) -> None:
        parser = build_github_parser()

        # 1. Test --domain bizid mapping
        args_bizid = parser.parse_args(["farm", "--domain", "bizid"])
        self.assertEqual(args_bizid.domain, "bizid")
        mapped_bizid = map_github_farm_args(args_bizid)
        self.assertIn("--email-domain", mapped_bizid)
        idx_bizid = mapped_bizid.index("--email-domain")
        self.assertEqual(mapped_bizid[idx_bizid + 1], "bizid")

        # 2. Test --domain myid mapping with options
        args_myid = parser.parse_args([
            "farm",
            "--domain", "myid",
            "--max-accounts", "3",
            "--delay-min", "25",
            "--delay-max", "50",
            "--headless",
            "--dry-run",
            "--proxy", "http://127.0.0.1:8888",
        ])
        mapped_myid = map_github_farm_args(args_myid)
        self.assertIn("--email-domain", mapped_myid)
        self.assertEqual(mapped_myid[mapped_myid.index("--email-domain") + 1], "myid")
        self.assertIn("--max-accounts", mapped_myid)
        self.assertEqual(mapped_myid[mapped_myid.index("--max-accounts") + 1], "3")
        self.assertIn("--delay-min", mapped_myid)
        self.assertEqual(mapped_myid[mapped_myid.index("--delay-min") + 1], "25.0")
        self.assertIn("--delay-max", mapped_myid)
        self.assertEqual(mapped_myid[mapped_myid.index("--delay-max") + 1], "50.0")
        self.assertIn("--headless", mapped_myid)
        self.assertIn("--dry-run", mapped_myid)
        self.assertIn("--proxy", mapped_myid)
        self.assertEqual(mapped_myid[mapped_myid.index("--proxy") + 1], "http://127.0.0.1:8888")

        # 3. Test default --domain binus
        args_binus = parser.parse_args(["farm", "--index", "5"])
        self.assertEqual(args_binus.domain, "binus")
        mapped_binus = map_github_farm_args(args_binus)
        self.assertIn("--index", mapped_binus)
        self.assertEqual(mapped_binus[mapped_binus.index("--index") + 1], "5")
        self.assertIn("--email-domain", mapped_binus)
        self.assertEqual(mapped_binus[mapped_binus.index("--email-domain") + 1], "binus")

    def test_verify_url_and_from_mail_parsing(self) -> None:
        parser = build_github_parser()

        # Direct SheerID URL
        url_input = "https://services.sheerid.com/verify/test-verification-id-12345/"
        args_url = parser.parse_args(["verify", "--url", url_input])
        self.assertEqual(args_url.github_cmd, "verify")
        self.assertEqual(args_url.url, url_input)
        self.assertFalse(args_url.from_mail)

        # From mailbox extraction
        args_mail = parser.parse_args([
            "verify",
            "--from-mail",
            "--timeout", "120",
            "--gateway",
            "--headless",
            "--debug",
            "--email", "test@binus.ac.id",
        ])
        self.assertEqual(args_mail.github_cmd, "verify")
        self.assertTrue(args_mail.from_mail)
        self.assertIsNone(args_mail.url)
        self.assertEqual(args_mail.timeout, 120)
        self.assertTrue(args_mail.gateway)
        self.assertTrue(args_mail.headless)
        self.assertTrue(args_mail.debug)
        self.assertEqual(args_mail.email, "test@binus.ac.id")

    def test_defensive_error_on_missing_verify_target(self) -> None:
        parser = build_github_parser()
        args = parser.parse_args(["verify"])

        f = io.StringIO()
        with patch("sys.stdout", f), patch("sys.stderr", f):
            code = cmd_github(args)

        self.assertEqual(code, 1)
        output = f.getvalue()
        self.assertIn("SheerID verification URL required", output)
        self.assertIn("--from-mail", output)
        self.assertIn("--url", output)

    def test_defensive_error_on_missing_subcommand(self) -> None:
        args = argparse.Namespace(github_cmd=None)
        f = io.StringIO()
        with patch("sys.stdout", f), patch("sys.stderr", f):
            code = cmd_github(args)

        self.assertEqual(code, 1)
        output = f.getvalue()
        self.assertIn("no github subcommand specified", output)


if __name__ == "__main__":
    unittest.main()
