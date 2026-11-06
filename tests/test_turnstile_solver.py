#!/usr/bin/env python3
"""tests/test_turnstile_solver.py — Verification for Turnstile Solver & IdP Sniffer."""
import math
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import turnstile_solver as TS


class TestTurnstileKinematics(unittest.TestCase):
    def test_minimum_jerk_bounds(self):
        self.assertAlmostEqual(TS._minimum_jerk_step(0.0), 0.0)
        self.assertAlmostEqual(TS._minimum_jerk_step(1.0), 1.0)
        self.assertAlmostEqual(TS._minimum_jerk_step(0.5), 0.5)
        # Monotonicity test
        prev = -1.0
        for i in range(11):
            val = TS._minimum_jerk_step(i / 10.0)
            self.assertGreaterEqual(val, prev)
            prev = val

    def test_approach_trajectory_generation(self):
        start = (100.0, 150.0)
        target = (500.0, 400.0)
        points = TS.generate_approach_trajectory(start[0], start[1], target[0], target[1], steps=20)
        self.assertEqual(len(points), 21)
        # First point should start close to start
        self.assertAlmostEqual(points[0][0], start[0], delta=2.0)
        self.assertAlmostEqual(points[0][1], start[1], delta=2.0)
        # Last point should reach the target
        self.assertAlmostEqual(points[-1][0], target[0], delta=0.1)
        self.assertAlmostEqual(points[-1][1], target[1], delta=0.1)


class TestIdPArchitectureSniffing(unittest.IsolatedAsyncioTestCase):
    async def test_detects_firebase_auth(self):
        fake_page = MagicMock()
        fake_page.content = AsyncMock(return_value="""
            <html>
                <script>
                    const firebaseConfig = {
                        apiKey: "AIzaDummyTestFixtureKeyForUnitTestsOnly01",
                        authDomain: "kildev.firebaseapp.com",
                        projectId: "kildev"
                    };
                </script>
            </html>
        """)
        idp = await TS.sniff_idp_architecture(fake_page)
        self.assertTrue(idp["has_idp"])
        self.assertEqual(idp["provider"], "firebase")
        self.assertEqual(idp["details"]["api_key"], "AIzaDummyTestFixtureKeyForUnitTestsOnly01")
        self.assertEqual(idp["details"]["project_id"], "kildev")

    async def test_detects_supabase_auth(self):
        fake_page = MagicMock()
        fake_page.content = AsyncMock(return_value="""
            <html>
                <script>
                    const client = createClient("https://xyzcompany123.supabase.co", "anon-key");
                </script>
            </html>
        """)
        idp = await TS.sniff_idp_architecture(fake_page)
        self.assertTrue(idp["has_idp"])
        self.assertEqual(idp["provider"], "supabase")
        self.assertEqual(idp["details"]["project_ref"], "xyzcompany123")


class TestTurnstileDiagnosis(unittest.IsolatedAsyncioTestCase):
    async def test_diagnose_standard_dom(self):
        fake_page = MagicMock()
        fake_el = MagicMock()
        fake_el.is_visible = AsyncMock(return_value=True)
        fake_el.get_attribute = AsyncMock(return_value="0x4AAAAAA_test_key")
        fake_page.query_selector = AsyncMock(return_value=fake_el)

        diag = await TS.diagnose_turnstile(fake_page)
        self.assertTrue(diag["present"])
        self.assertEqual(diag["mode"], "dom-standard")
        self.assertEqual(diag["sitekey"], "0x4AAAAAA_test_key")


if __name__ == "__main__":
    unittest.main()
