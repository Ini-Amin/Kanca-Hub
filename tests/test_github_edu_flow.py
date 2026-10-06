"""Unit tests for the one-shot 'kancahub github edu' flow.

Stdlib unittest only, no network, no gateway spawns:
- 'edu' subcommand exists and its flags parse with the agreed defaults;
- build_edu_steps() returns the ordered stages (and drops verify on --no-verify);
- edu_needs_human()/edu_doc_upload_attempted() marker logic;
- missing-tool paths print a clear error and return 1 (mocked, offline).
"""

from __future__ import annotations

import argparse
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import kancahub
from scripts.kancahub import (
    EgressChoice,
    build_edu_steps,
    build_github_parser,
    build_parser,
    cmd_github,
    edu_doc_upload_attempted,
    edu_needs_human,
)

class TestEduParser(unittest.TestCase):
    def test_edu_subcommand_exists(self) -> None:
        parser = build_github_parser()
        sub = next(
            (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)),
            None,
        )
        self.assertIsNotNone(sub)
        self.assertIn("edu", sub.choices)

    def test_edu_parses_in_full_kancahub_parser(self) -> None:
        kp = build_parser()
        args = kp.parse_args(["github", "edu"])
        self.assertEqual(args.group, "github")
        self.assertEqual(args.github_cmd, "edu")

    def test_edu_flag_defaults(self) -> None:
        parser = build_github_parser()
        args = parser.parse_args(["edu"])
        self.assertEqual(args.domain, "bizid")
        self.assertEqual(args.inbox, "relay")
        self.assertEqual(args.proxy, "auto")
        self.assertFalse(args.interactive)
        self.assertFalse(args.no_verify)
        self.assertIsNone(args.url)
        self.assertEqual(args.max_accounts, 1)

    def test_edu_flags_parse(self) -> None:
        parser = build_github_parser()
        url = "https://services.sheerid.com/verify/abc-123/"
        args = parser.parse_args([
            "edu",
            "--domain", "binus",
            "--inbox", "binus",
            "--proxy", "http://127.0.0.1:8888",
            "--interactive",
            "--no-verify",
            "--url", url,
            "--max-accounts", "2",
        ])
        self.assertEqual(args.github_cmd, "edu")
        self.assertEqual(args.domain, "binus")
        self.assertEqual(args.inbox, "binus")
        self.assertEqual(args.proxy, "http://127.0.0.1:8888")
        self.assertTrue(args.interactive)
        self.assertTrue(args.no_verify)
        self.assertEqual(args.url, url)
        self.assertEqual(args.max_accounts, 2)

class TestBuildEduSteps(unittest.TestCase):
    def test_full_flow_order(self) -> None:
        self.assertEqual(build_edu_steps(False), ["farm", "verify", "human_pause"])

    def test_no_verify_drops_verify_and_pause(self) -> None:
        self.assertEqual(build_edu_steps(True), ["farm"])

    def test_returns_fresh_list(self) -> None:
        a = build_edu_steps()
        a.append("junk")
        self.assertEqual(build_edu_steps(), ["farm", "verify", "human_pause"])

class TestEduMarkerHelpers(unittest.TestCase):
    def test_needs_human_markers(self) -> None:
        self.assertTrue(edu_needs_human("Please upload a photo of your student ID"))
        self.assertTrue(edu_needs_human("camera capture required"))
        self.assertTrue(edu_needs_human("phone verification step"))
        self.assertFalse(edu_needs_human("Verifikasi dikirim! Tunggu review."))
        self.assertFalse(edu_needs_human(""))

    def test_doc_upload_markers(self) -> None:
        self.assertTrue(edu_doc_upload_attempted("-> Langkah 4/4: Mengupload dokumen guru..."))
        self.assertTrue(edu_doc_upload_attempted("[OK] Dokumen diupload!"))
        self.assertTrue(edu_doc_upload_attempted("BERHASIL! AUTO-PASS!"))
        self.assertFalse(edu_doc_upload_attempted("URL tidak valid"))

class TestEduMissingTool(unittest.TestCase):
    """Missing tool -> clear error + exit 1, no traceback, nothing executed."""

    def _args(self, **over):
        base = dict(github_cmd="edu", domain="bizid", inbox="relay", proxy="auto",
                    interactive=False, no_verify=False, url=None, max_accounts=1)
        base.update(over)
        return argparse.Namespace(**base)

    def test_missing_farm_tool(self) -> None:
        f = io.StringIO()
        with patch.object(kancahub, "AUTO_FREECF", Path("/nonexistent-repo")), \
             patch("sys.stdout", f), patch("sys.stderr", f):
            code = cmd_github(self._args())
        self.assertEqual(code, 1)
        out = f.getvalue()
        self.assertIn("github_farm.py not found", out)

    def test_no_verify_reaches_farm_only(self) -> None:
        # --no-verify: with a missing farm tool it must still stop at stage 1
        f = io.StringIO()
        with patch.object(kancahub, "AUTO_FREECF", Path("/nonexistent-repo")), \
             patch("sys.stdout", f), patch("sys.stderr", f):
            code = cmd_github(self._args(no_verify=True))
        self.assertEqual(code, 1)
        self.assertIn("github_farm.py not found", f.getvalue())

    def test_missing_verify_target_reports_clear_error(self) -> None:
        # Farm tool "exists" (temp repo), farm run mocked to succeed with one new
        # account, then the SheerID URL step must fail cleanly: the finder file
        # is missing in the fake repo -> clear error, exit 1.
        import tempfile

        f = io.StringIO()
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "scripts").mkdir()
            (repo / "scripts" / "github_farm.py").write_text("# fake\n")
            (repo / "github_accounts.json").write_text(
                '{"accounts": [{"email": "t@x.y"}]}')

            def fake_run(cmd, cwd=None, env=None):
                return 0  # farm "succeeds"; account count delta comes from the file

            with patch.object(kancahub, "AUTO_FREECF", repo), \
                 patch.object(kancahub, "_choose_egress",
                              return_value=EgressChoice(None, "direct", direct=True)), \
                 patch.object(kancahub, "run", side_effect=fake_run), \
                 patch("sys.stdout", f), patch("sys.stderr", f):
                # First call sees 0 accounts (before); simulate the farm adding
                # one by writing the file *after* the before-count... simplest:
                # count before == 1 and after == 1 -> account=False but stage
                # continues; the verify-stage error is what we assert.
                code = cmd_github(self._args(url=None))
        out = f.getvalue()
        self.assertEqual(code, 1)
        self.assertIn("sheerid_link_finder.py not found", out)
        self.assertIn("could not obtain a SheerID verification URL", out)

if __name__ == "__main__":
    unittest.main()
