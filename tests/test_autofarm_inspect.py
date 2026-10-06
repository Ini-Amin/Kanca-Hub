"""Tests for autofarm inspect-first auth detection + honest success gating.

All offline: the pure classifier, the parser, and a mocked browser flow.
No network, no Camoufox, no account creation.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import autofarm  # noqa: E402


class TestClassifyAuthTruthTable(unittest.TestCase):
    """classify_auth is pure: labels + hrefs + input flags -> methods dict."""

    def test_github_only(self):
        got = autofarm.classify_auth(["Continue with GitHub"], [], False, False)
        self.assertEqual(got["methods"], ["github"])
        self.assertFalse(got["has_email_form"])
        self.assertEqual(got["preferred"], "github")

    def test_github_via_oauth_href_without_text(self):
        got = autofarm.classify_auth(
            ["Sign in"], ["https://github.com/login/oauth/authorize?client_id=x"], False, False)
        self.assertEqual(got["methods"], ["github"])

    def test_google_only(self):
        got = autofarm.classify_auth(["Sign in with Google"], [], False, False)
        self.assertEqual(got["methods"], ["google"])
        self.assertEqual(got["preferred"], "google")

    def test_google_via_oauth_href(self):
        got = autofarm.classify_auth(
            [], ["https://accounts.google.com/o/oauth2/v2/auth?x=1"], False, False)
        self.assertEqual(got["methods"], ["google"])

    def test_email_only(self):
        got = autofarm.classify_auth([], [], True, True)
        self.assertEqual(got["methods"], ["email"])
        self.assertTrue(got["has_email_form"])
        self.assertEqual(got["preferred"], "email")

    def test_email_requires_both_inputs(self):
        self.assertEqual(autofarm.classify_auth([], [], True, False)["methods"], [])
        self.assertEqual(autofarm.classify_auth([], [], False, True)["methods"], [])
        self.assertFalse(autofarm.classify_auth([], [], True, False)["has_email_form"])

    def test_github_plus_email_prefers_github(self):
        got = autofarm.classify_auth(["Continue with GitHub"], [], True, True)
        self.assertEqual(got["methods"], ["github", "email"])
        self.assertEqual(got["preferred"], "github")
        self.assertTrue(got["has_email_form"])

    def test_all_three_ordered_github_google_email(self):
        got = autofarm.classify_auth(
            ["Continue with GitHub", "Continue with Google"], [], True, True)
        self.assertEqual(got["methods"], ["github", "google", "email"])

    def test_nothing_detected(self):
        got = autofarm.classify_auth([], [], False, False)
        self.assertEqual(got["methods"], [])
        self.assertIsNone(got["preferred"])
        self.assertFalse(got["has_email_form"])

    def test_case_insensitive_and_whitespace_tolerant(self):
        got = autofarm.classify_auth(["  CONTINUE WITH GITHUB  "], [], False, False)
        self.assertEqual(got["methods"], ["github"])

    def test_none_values_are_tolerated(self):
        got = autofarm.classify_auth([None, ""], [None], False, False)
        self.assertEqual(got["methods"], [])


class TestParserFlags(unittest.TestCase):
    def test_inspect_only_flag_parses(self):
        args = autofarm.build_parser().parse_args(["https://example.com", "--inspect-only"])
        self.assertTrue(args.inspect_only)
        self.assertEqual(args.url, "https://example.com")

    def test_inspect_only_defaults_off(self):
        args = autofarm.build_parser().parse_args(["https://example.com"])
        self.assertFalse(args.inspect_only)

    def test_existing_flags_still_parse(self):
        args = autofarm.build_parser().parse_args(
            ["https://x.test", "--domain", "kancalabs.my.id", "--headless",
             "--proxy", "none", "--inject-9router", "--out", "/tmp/o.json"])
        self.assertEqual(args.domain, "kancalabs.my.id")
        self.assertTrue(args.headless and args.inject_9router)
        self.assertEqual(args.proxy, "none")
        self.assertEqual(args.out, "/tmp/o.json")


class TestGithubAccountLoader(unittest.TestCase):
    def test_missing_file_yields_empty(self):
        self.assertEqual(autofarm.load_github_accounts("/nonexistent/nope.json"), [])

    def test_loads_valid_accounts_from_tmp(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "gh.json"
            p.write_text(json.dumps({"accounts": [
                {"username": "u1", "password": "p1"},
                {"email": "a@b.c", "password": "p2"},
                {"username": "nopass"},            # dropped: no password
                "not-a-dict",                       # dropped
            ]}))
            accs = autofarm.load_github_accounts(p)
        self.assertEqual(len(accs), 2)


class TestInspectOnlyWritesNothing(unittest.TestCase):
    """--inspect-only must not touch the results file or scaffold."""

    def test_inspect_only_writes_no_success_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "accounts.json"

            async def fake_flow(url, headless, proxy_cfg, email, password, username,
                                host, result, *, inspect_only=False):
                result["auth_methods"] = ["github", "email"]
                result["auth_preferred"] = "github"
                result["has_email_form"] = True
                result["stopped_at"] = "inspect_only"
                return result

            with patch.object(autofarm, "_run_browser_flow", side_effect=fake_flow), \
                    patch.object(autofarm, "get_fresh_proxy", return_value=None), \
                    patch.object(autofarm, "sync_now", return_value=0), \
                    patch.object(autofarm, "_scaffold_pipeline") as scaffold:
                result = asyncio.run(autofarm.run_autofarm(
                    "https://target.test/login",
                    proxy="none",
                    out_json=out,
                    inspect_only=True,
                ))

            self.assertFalse(out.exists(), "inspect-only must not write the results file")
            scaffold.assert_not_called()
            self.assertEqual(result["auth_methods"], ["github", "email"])


class TestNoSuccessFileWhenBlocked(unittest.TestCase):
    """Blocked / no-method / no-signal runs must write success:false and no scaffold."""

    def _run(self, fake_flow, tmpdir):
        out = Path(tmpdir) / "accounts.json"
        with patch.object(autofarm, "_run_browser_flow", side_effect=fake_flow), \
                patch.object(autofarm, "get_fresh_proxy", return_value=None), \
                patch.object(autofarm, "sync_now", return_value=0), \
                patch.object(autofarm, "_scaffold_pipeline") as scaffold:
            result = asyncio.run(autofarm.run_autofarm(
                "https://target.test/signup", proxy="none", out_json=out))
        return out, result, scaffold

    def test_no_auth_method_writes_false_and_no_scaffold(self):
        import tempfile

        async def fake_flow(url, headless, proxy_cfg, email, password, username,
                            host, result, *, inspect_only=False):
            result["auth_methods"] = []
            result["stopped_at"] = "no_supported_auth_method"
            result["error"] = "no supported auth method found"
            return result

        with tempfile.TemporaryDirectory() as d:
            out, result, scaffold = self._run(fake_flow, d)
            self.assertFalse(result["success"])
            scaffold.assert_not_called()
            self.assertTrue(out.exists())
            saved = json.loads(out.read_text())
            self.assertFalse(saved[-1]["success"])

    def test_submitted_but_no_signal_is_not_success(self):
        import tempfile

        async def fake_flow(url, headless, proxy_cfg, email, password, username,
                            host, result, *, inspect_only=False):
            result["auth_methods"] = ["email"]
            result["has_email_form"] = True
            result["stopped_at"] = "no_post_login_signal"
            result["error"] = "submitted but no post-login signal"
            result["success"] = False
            return result

        with tempfile.TemporaryDirectory() as d:
            out, result, scaffold = self._run(fake_flow, d)
            self.assertFalse(result["success"])
            scaffold.assert_not_called()
            saved = json.loads(out.read_text())
            self.assertFalse(saved[-1]["success"])

    def test_real_success_writes_true_and_scaffolds(self):
        import tempfile

        async def fake_flow(url, headless, proxy_cfg, email, password, username,
                            host, result, *, inspect_only=False):
            result["auth_methods"] = ["email"]
            result["has_email_form"] = True
            result["stopped_at"] = "post_login_verified"
            result["success"] = True
            return result

        with tempfile.TemporaryDirectory() as d:
            out, result, scaffold = self._run(fake_flow, d)
            self.assertTrue(result["success"])
            scaffold.assert_called_once()
            saved = json.loads(out.read_text())
            self.assertTrue(saved[-1]["success"])


class TestPostLoginGateFallback(unittest.TestCase):
    """The local fallback signal must reject login pages and provider domains."""

    def test_rejects_still_on_login_path(self):
        self.assertFalse(autofarm._post_login_signal(
            "https://target.test/login", {}, "https://target.test/login", "target.test"))

    def test_rejects_third_party_provider_host(self):
        self.assertFalse(autofarm._post_login_signal(
            "https://github.com/sessions/two-factor", {}, "https://target.test/login", "target.test"))

    def test_accepts_off_login_path_on_same_host(self):
        self.assertTrue(autofarm._post_login_signal(
            "https://target.test/dashboard", {}, "https://target.test/login", "target.test"))


if __name__ == "__main__":
    unittest.main()