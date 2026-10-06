"""Unit tests for the unified interactive menu in scripts/kancahub.py."""

from __future__ import annotations

import argparse
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, call, patch

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.kancahub import (
    MENU_STAGE_HEADERS,
    UNIFIED_MENU,
    beginner_entry,
    build_parser,
    interactive_mode,
    menu_entry,
    render_menu,
    run_end_to_end_flow,
)


class TestUnifiedMenuStructure(unittest.TestCase):
    """Test the UNIFIED_MENU structure and parity against the old 2-page union."""

    def test_single_list_no_page_concept(self) -> None:
        # Assert UNIFIED_MENU is a flat single list
        self.assertIsInstance(UNIFIED_MENU, list)
        self.assertGreater(len(UNIFIED_MENU), 0)

        # Ensure no page-related variables remain in menu items
        for item in UNIFIED_MENU:
            self.assertEqual(len(item), 4, f"Item {item} should be a 4-tuple (key, desc, cmd_str, cmd_args)")
            key, desc, cmd_str, cmd_args = item
            self.assertIsInstance(key, str)
            self.assertIsInstance(desc, str)
            self.assertIsInstance(cmd_str, str)
            self.assertTrue(cmd_args is None or isinstance(cmd_args, list))

    def test_end_to_end_option_exists(self) -> None:
        # Check that top option "0" is the guided end-to-end setup
        top_item = UNIFIED_MENU[0]
        self.assertEqual(top_item[0], "0")
        self.assertEqual(top_item[2], "end-to-end")
        self.assertIn("end-to-end", top_item[1].lower())

    def test_parity_against_old_pages_union(self) -> None:
        # Old Page 1 command strings.
        # NOTE: 'proxy start' was intentionally changed to 'proxy gateway' so the
        # menu item is NON-INTERACTIVE and can run as a background job (the guided
        # 'proxy start' prompts for a mode, which would block a background job).
        # The feature (start a proxy gateway) is preserved under the new command.
        old_page1_commands = {
            "stack signup",
            "doctor",
            "proxy gateway",
            "thk batch",
            "grok run",
            "k12 auto",
            "warp",
            "github farm",
            "grok inject",
        }
        # Old Page 2 command strings
        old_page2_commands = {
            "mail test",
            "mail otp",
            "gmail farm",
            "thk setup-env",
            "stack login",
            "stack sync --prune",
            "proxy harvest",
            "k12 link-finder",
            "yowes list",
        }
        old_union = old_page1_commands | old_page2_commands
        self.assertEqual(len(old_union), 18)

        # Extract current command strings
        current_commands = {item[2] for item in UNIFIED_MENU if item[2] != "end-to-end"}

        # Parity check: every single previously-listed command must still be present
        missing = old_union - current_commands
        self.assertEqual(missing, set(), f"Commands missing from unified menu: {missing}")
        self.assertTrue(old_union.issubset(current_commands))

        # Total items: 18 old commands + 1 end-to-end option = 19
        self.assertEqual(len(UNIFIED_MENU), 19)

        # Keys must be sequential strings from "0" to "18"
        expected_keys = [str(i) for i in range(19)]
        actual_keys = [item[0] for item in UNIFIED_MENU]
        self.assertEqual(actual_keys, expected_keys)

    def test_stage_headers_coverage(self) -> None:
        self.assertIn("0", MENU_STAGE_HEADERS)
        self.assertIn("1", MENU_STAGE_HEADERS)
        self.assertIn("5", MENU_STAGE_HEADERS)
        self.assertIn("12", MENU_STAGE_HEADERS)
        self.assertIn("15", MENU_STAGE_HEADERS)


class TestRenderMenu(unittest.TestCase):
    """Test non-interactive rendering of the unified menu."""

    def test_render_menu_output(self) -> None:
        output = render_menu()
        self.assertIsInstance(output, str)

        # Check all command strings are in the rendered text
        for _, desc, cmd_str, _ in UNIFIED_MENU:
            self.assertIn(cmd_str, output)

        # Check stage headers are rendered
        for _, header in MENU_STAGE_HEADERS.items():
            self.assertIn(header, output)

        # Check footer
        import re
        plain = re.sub(r"\x1b\[[0-9;]*m", "", output)
        self.assertIn("[h] Help & command reference", plain)
        self.assertIn("[q] Exit", plain)

        # Ensure pagination strings do NOT appear
        self.assertNotIn("page 1/2", output)
        self.assertNotIn("page 2/2", output)
        self.assertNotIn("Switch to page", output)


class TestInteractiveMode(unittest.TestCase):
    """Test interactive_mode prompt actions."""

    @patch("builtins.input", side_effect=["q"])
    def test_quit_returns_zero(self, _mock_input: MagicMock) -> None:
        p = build_parser()
        rc = interactive_mode(p)
        self.assertEqual(rc, 0)

    @patch("builtins.input", side_effect=["h"])
    def test_help_prints_help_and_exits(self, _mock_input: MagicMock) -> None:
        p = build_parser()
        with patch.object(p, "print_help") as mock_help:
            rc = interactive_mode(p)
            self.assertEqual(rc, 0)
            mock_help.assert_called_once()

    @patch("scripts.kancahub.dispatch", return_value=0)
    @patch("builtins.input", side_effect=["1", "", "q"])
    def test_select_item_dispatches_command(
        self,
        _mock_input: MagicMock,
        mock_dispatch: MagicMock,
    ) -> None:
        p = build_parser()
        rc = interactive_mode(p)
        self.assertEqual(rc, 0)
        self.assertTrue(mock_dispatch.called)
        # Item "1" maps to ["doctor"]
        args = mock_dispatch.call_args[0][1]
        self.assertEqual(args.group, "doctor")

    @patch("scripts.kancahub.run_end_to_end_flow", return_value=0)
    @patch("builtins.input", side_effect=["0", "", "q"])
    def test_select_zero_runs_end_to_end_flow(
        self,
        _mock_input: MagicMock,
        mock_e2e: MagicMock,
    ) -> None:
        p = build_parser()
        rc = interactive_mode(p)
        self.assertEqual(rc, 0)
        mock_e2e.assert_called_once_with(p)


class TestRunEndToEndFlow(unittest.TestCase):
    """Test sequential execution, stops on failure, and honest reporting."""

    def test_cancel_returns_zero(self) -> None:
        p = build_parser()
        rc = run_end_to_end_flow(p, farm_choice="c")
        self.assertEqual(rc, 0)

    @patch("scripts.kancahub.dispatch")
    def test_stops_if_egress_fails_and_proxy_declined(self, mock_dispatch: MagicMock) -> None:
        p = build_parser()
        # Step 1 proxy verify returns 1 (failed)
        mock_dispatch.return_value = 1
        with patch("builtins.input", return_value="n"):
            rc = run_end_to_end_flow(p, farm_choice="1")
            self.assertEqual(rc, 1)
            # Should not run account farm
            self.assertEqual(mock_dispatch.call_count, 1)

    @patch("scripts.kancahub.dispatch")
    def test_stops_if_account_farm_fails(self, mock_dispatch: MagicMock) -> None:
        p = build_parser()
        # Step 1 proxy verify succeeds (0), Step 2 farm fails (2)
        mock_dispatch.side_effect = [0, 2]
        rc = run_end_to_end_flow(p, farm_choice="2")
        self.assertEqual(rc, 2)
        # Should not proceed to inject (step 3) or sync (step 4)
        self.assertEqual(mock_dispatch.call_count, 2)

    @patch("scripts.kancahub.dispatch")
    def test_stops_if_inject_fails(self, mock_dispatch: MagicMock) -> None:
        p = build_parser()
        # Step 1 verify (0), Step 2 farm (0), Step 3 inject (3)
        mock_dispatch.side_effect = [0, 0, 3]
        rc = run_end_to_end_flow(p, farm_choice="2")
        self.assertEqual(rc, 3)
        # Should not proceed to sync (step 4)
        self.assertEqual(mock_dispatch.call_count, 3)

    @patch("scripts.kancahub.dispatch")
    def test_success_runs_full_sequence(self, mock_dispatch: MagicMock) -> None:
        p = build_parser()
        # Farm 2 (Cloudflare): verify -> signup -> inject -> sync (all 0)
        mock_dispatch.side_effect = [0, 0, 0, 0]
        rc = run_end_to_end_flow(p, farm_choice="2")
        self.assertEqual(rc, 0)
        self.assertEqual(mock_dispatch.call_count, 4)


class TestBeginnerAndMenuEntry(unittest.TestCase):
    """Test beginner_entry fallback and menu_entry wiring."""

    @patch("scripts.kancahub.interactive_mode", return_value=0)
    def test_menu_entry_calls_interactive_mode(self, mock_interactive: MagicMock) -> None:
        p = build_parser()
        rc = menu_entry(p)
        self.assertEqual(rc, 0)
        mock_interactive.assert_called_once_with(p)

    @patch("scripts.kancahub.interactive_mode", return_value=0)
    def test_beginner_entry_fallback(self, mock_interactive: MagicMock) -> None:
        p = build_parser()
        with patch.dict("sys.modules", {"beginner": None}):
            rc = beginner_entry(p)
            self.assertEqual(rc, 0)
            mock_interactive.assert_called_once_with(p)


if __name__ == "__main__":
    unittest.main()
