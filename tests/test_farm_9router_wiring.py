"""Wiring tests for scripts/farm_9router.py (stdlib unittest, no network)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.farm_9router import (
    DEFAULT_ROUTER_URL,
    DEFAULT_SHIM_PORT,
    FARM_TOKENMIX,
    FARM_ZEROTWO,
    build_child_cmd,
    build_parser,
    check_farm_ready,
    normalize_proxy_for,
    router_base,
)

class TestParser(unittest.TestCase):
    def test_subcommands(self):
        ap = build_parser()
        # parse both farms; unknown farm must fail
        for farm in (FARM_TOKENMIX, FARM_ZEROTWO):
            ns = ap.parse_args([farm])
            self.assertEqual(ns.farm, farm)
        with self.assertRaises(SystemExit):
            ap.parse_args(["nope"])

    def test_proxy_default_auto(self):
        ap = build_parser()
        for farm in (FARM_TOKENMIX, FARM_ZEROTWO):
            ns = ap.parse_args([farm])
            self.assertEqual(ns.proxy, "auto")

    def test_router_url_default(self):
        ap = build_parser()
        ns = ap.parse_args([FARM_ZEROTWO])
        self.assertEqual(ns.router_url, DEFAULT_ROUTER_URL)
        self.assertEqual(ns.router_url, "http://localhost:20128/v1")
        self.assertEqual(ns.shim_port, DEFAULT_SHIM_PORT)

class TestRouterBase(unittest.TestCase):
    def test_strips_v1(self):
        self.assertEqual(router_base("http://localhost:20128/v1"),
                         "http://localhost:20128")

    def test_leaves_bare(self):
        self.assertEqual(router_base("http://localhost:20128"),
                         "http://localhost:20128")

    def test_custom_host(self):
        self.assertEqual(router_base("http://10.0.0.5:9999/v1"),
                         "http://10.0.0.5:9999")

class TestNormalizeProxy(unittest.TestCase):
    def test_tokenmix_keeps_url(self):
        p = "http://user:pass@1.2.3.4:8080"
        self.assertEqual(normalize_proxy_for(FARM_TOKENMIX, p), p)

    def test_zerotwo_converts_url_to_colon_form(self):
        p = "http://user:pass@1.2.3.4:8080"
        self.assertEqual(normalize_proxy_for(FARM_ZEROTWO, p), "1.2.3.4:8080:user:pass")

    def test_zerotwo_hostport(self):
        self.assertEqual(normalize_proxy_for(FARM_ZEROTWO, "1.2.3.4:8080"), "1.2.3.4:8080")

    def test_none_passthrough(self):
        self.assertIsNone(normalize_proxy_for(FARM_TOKENMIX, None))
        self.assertIsNone(normalize_proxy_for(FARM_ZEROTWO, None))

class TestBuildChildCmd(unittest.TestCase):
    def test_zerotwo_full_argv(self):
        cmd = build_child_cmd(
            FARM_ZEROTWO, "http://user:pass@1.2.3.4:8080",
            "http://localhost:20128/v1", count=3, shim_port=8787,
            python="/x/py")
        self.assertEqual(cmd, [
            "/x/py", "-m", "ztharvester.cli", "run",
            "-n", "3",
            "--router-url", "http://localhost:20128",
            "--shim-base-url", "http://localhost:8787/v1",
            "--concurrency", "1",
            "--proxy", "1.2.3.4:8080:user:pass",
        ])

    def test_zerotwo_no_proxy_omits_flag(self):
        cmd = build_child_cmd(FARM_ZEROTWO, None, python="/x/py")
        self.assertNotIn("--proxy", cmd)
        self.assertIn("--concurrency", cmd)
        self.assertEqual(cmd[cmd.index("--concurrency") + 1], "1")

    def test_tokenmix_full_argv(self):
        cmd = build_child_cmd(
            FARM_TOKENMIX, "http://user:pass@1.2.3.4:8080",
            "http://localhost:20128/v1", count=2, python="/y/py")
        self.assertEqual(cmd, [
            "/y/py", "-m", "tokenmix_bulk",
            "-n", "2",
            "-c", "1",
            "--delay-min", "20", "--delay-max", "45",
            "--proxy", "http://user:pass@1.2.3.4:8080",
        ])

    def test_tokenmix_no_proxy_omits_flag(self):
        cmd = build_child_cmd(FARM_TOKENMIX, None, python="/y/py")
        self.assertNotIn("--proxy", cmd)
        self.assertIn("--delay-min", cmd)

    def test_unknown_farm_raises(self):
        with self.assertRaises(ValueError):
            build_child_cmd("bogus", None)

class TestCheckFarmReady(unittest.TestCase):
    def test_missing_repo_and_venv_reported(self):
        problems = check_farm_ready(
            FARM_TOKENMIX, repo=Path("/nonexistent/repo"),
            python="/nonexistent/python")
        self.assertEqual(len(problems), 2)
        self.assertTrue(any("repo missing" in p for p in problems))
        self.assertTrue(any("venv python missing" in p for p in problems))

    def test_real_install_ready(self):
        # On this machine both farms are installed; skip silently if not.
        for farm in (FARM_TOKENMIX, FARM_ZEROTWO):
            problems = check_farm_ready(farm)
            if problems:
                self.skipTest(f"{farm} not installed here: {problems}")
        self.assertEqual(check_farm_ready(FARM_TOKENMIX), [])
        self.assertEqual(check_farm_ready(FARM_ZEROTWO), [])

if __name__ == "__main__":
    unittest.main()
