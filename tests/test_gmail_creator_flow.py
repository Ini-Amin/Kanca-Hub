"""Behavioral test for browser initialization and navigation ordering in create_one."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.gmail_creator import SIGNUP_URL, FlowError, Settings, create_one


class TestGmailCreatorFlow(unittest.TestCase):
    def setUp(self):
        self.call_order: list[tuple[str, ...]] = []

        self.mock_tab = MagicMock()

        async def tab_get(url: str):
            self.call_order.append(("tab.get", url))

        self.mock_tab.get = AsyncMock(side_effect=tab_get)
        self.mock_tab.evaluate = AsyncMock(return_value=None)
        self.mock_tab.find = AsyncMock(return_value=None)

        self.mock_browser = MagicMock()

        async def browser_get(url: str):
            self.call_order.append(("browser.get", url))
            return self.mock_tab

        self.mock_browser.get = AsyncMock(side_effect=browser_get)
        self.mock_browser.stop = MagicMock()

        self.mock_uc = MagicMock()

        async def uc_start(**kwargs):
            self.call_order.append(("uc.start", kwargs.get("user_data_dir")))
            return self.mock_browser

        self.mock_uc.start = AsyncMock(side_effect=uc_start)

        self.st = Settings(
            src=Path("/tmp"),
            names=["Alex Morgan"],
            user_agents=[],
            file_password="TestPassword123!",
            birthday=(1, 15, 1995),
            gender="1",
            fivesim_key=None,
        )

    def test_navigation_order_contract_with_warming(self):
        """browser.get('about:blank') must precede warm_session navigation to google.com,

        which in turn must precede browser.get(SIGNUP_URL).
        """
        args = argparse.Namespace(
            headless=True,
            use_ua_file=False,
            warm=True,
            reuse_profile=None,
            random_password=False,
            password="SecretPassword123!",
        )

        async def fake_warm_session(tab):
            self.call_order.append(("warm_session", tab))
            await tab.get("https://www.google.com/")

        async def run_test():
            with patch("scripts.gmail_creator.warm_session", new=AsyncMock(side_effect=fake_warm_session)), \
                 patch("scripts.gmail_creator.sleep", new=AsyncMock()), \
                 patch("scripts.gmail_creator.wait_state", new=AsyncMock(side_effect=FlowError("stop after warmup"))):
                return await create_one(self.mock_uc, self.st, args, proxy=None, chrome=None)

        asyncio.run(run_test())

        # Verify call events
        events = [call[0] for call in self.call_order]
        self.assertIn("uc.start", events)
        self.assertIn("browser.get", events)
        self.assertIn("warm_session", events)
        self.assertIn("tab.get", events)

        # Retrieve indexes to assert chronological order
        idx_uc_start = next(i for i, call in enumerate(self.call_order) if call[0] == "uc.start")
        idx_about_blank = self.call_order.index(("browser.get", "about:blank"))
        idx_warm_session = next(i for i, call in enumerate(self.call_order) if call[0] == "warm_session")
        idx_google_nav = self.call_order.index(("tab.get", "https://www.google.com/"))
        idx_signup_nav = self.call_order.index(("browser.get", SIGNUP_URL))

        # 1. browser starts before tab is created
        self.assertLess(idx_uc_start, idx_about_blank)
        # 2. tab is created with about:blank BEFORE warm_session receives it
        self.assertLess(idx_about_blank, idx_warm_session)
        # 3. warm_session receives the created tab
        warm_call = next(call for call in self.call_order if call[0] == "warm_session")
        self.assertIs(warm_call[1], self.mock_tab)
        # 4. google.com navigation happens during/as part of warm_session
        self.assertLess(idx_warm_session, idx_google_nav)
        # 5. warm_session navigations finish before navigating to signup URL
        self.assertLess(idx_google_nav, idx_signup_nav)

        # Also verify browser was stopped cleanly in finally block
        self.mock_browser.stop.assert_called_once()

    def test_navigation_order_contract_without_warming(self):
        """When warm=False, about:blank is fetched then directly SIGNUP_URL with no warmup."""
        args = argparse.Namespace(
            headless=True,
            use_ua_file=False,
            warm=False,
            reuse_profile=None,
            random_password=False,
            password="SecretPassword123!",
        )

        async def run_test():
            with patch("scripts.gmail_creator.sleep", new=AsyncMock()), \
                 patch("scripts.gmail_creator.wait_state", new=AsyncMock(side_effect=FlowError("stop after signup get"))):
                return await create_one(self.mock_uc, self.st, args, proxy=None, chrome=None)

        asyncio.run(run_test())

        browser_gets = [call[1] for call in self.call_order if call[0] == "browser.get"]
        self.assertEqual(browser_gets, ["about:blank", SIGNUP_URL])
        tab_gets = [call[1] for call in self.call_order if call[0] == "tab.get"]
        self.assertEqual(tab_gets, [], "warm_session should not navigate when warm=False")

    def test_furthest_step_reported_on_failure(self):
        """When phone-gated after the name step, log shows the actual step reached."""
        args = argparse.Namespace(
            headless=True,
            use_ua_file=False,
            warm=False,
            reuse_profile=None,
            random_password=False,
            password="SecretPassword123!",
        )

        logs: list[str] = []
        state_sequence = iter(["name", "phone"])

        async def fake_wait_state(tab, expected, timeout=10):
            return next(state_sequence)

        async def run_test():
            with patch("scripts.gmail_creator.log", side_effect=logs.append), \
                 patch("scripts.gmail_creator.sleep", new=AsyncMock()), \
                 patch("scripts.gmail_creator.type_into", new=AsyncMock(return_value=True)), \
                 patch("scripts.gmail_creator.next_step", new=AsyncMock(return_value=True)), \
                 patch("scripts.gmail_creator.wait_state", new=AsyncMock(side_effect=fake_wait_state)):
                return await create_one(self.mock_uc, self.st, args, proxy=None, chrome=None)

        asyncio.run(run_test())

        logged_text = "\n".join(logs)
        self.assertIn("last step reached: name", logged_text)
        self.assertNotIn("never got past the username step", logged_text)


if __name__ == "__main__":
    unittest.main()
