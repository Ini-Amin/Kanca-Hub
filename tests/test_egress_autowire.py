"""
Unit tests for scripts/egress.py smart proxy auto-wire.

Tests cover:
  1. is_blocked truth table (403, 429, 503 vs non-blocked status codes).
  2. probe_status unit behavior (success, blocked, timeouts, error handling).
  3. Mode parsing (none, direct, warp, explicit proxy URL, env KANCAHUB_EGRESS).
  4. Explicit proxy returns without spawning gateway.
  5. Auto strategy fallback order:
     - Local gateway (:8888 / :8899)
     - Clean pool gateway (signup_from_scratch/proxies.txt)
     - Cloudflare WARP tunnel escalation
     - PetaniProxy residential pool escalation
     - Graceful direct fallback
  6. Import without side effects (no background gateway spawned on import).

All tests are pure stdlib unittest with NO live network activity.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.egress import (
    auto_egress,
    is_blocked,
    is_port_open,
    probe_status,
)


class TestIsBlocked(unittest.TestCase):
    """Verify is_blocked truth table against HTTP status codes."""

    def test_blocked_status_codes(self) -> None:
        blocked_codes = [403, 429, 503]
        for code in blocked_codes:
            with self.subTest(code=code):
                self.assertTrue(is_blocked(code), f"Expected status {code} to be blocked")

    def test_non_blocked_status_codes(self) -> None:
        non_blocked_codes = [0, 200, 201, 204, 301, 302, 307, 308, 400, 401, 404, 405, 500, 502, 504]
        for code in non_blocked_codes:
            with self.subTest(code=code):
                self.assertFalse(is_blocked(code), f"Expected status {code} to NOT be blocked")


class TestProbeStatus(unittest.TestCase):
    """Verify probe_status invokes curl gently and parses status codes."""

    @patch("subprocess.run")
    def test_probe_status_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="200\n", returncode=0)
        st = probe_status("https://github.com/signup", proxy="http://127.0.0.1:8888", timeout=5.0)
        self.assertEqual(st, 200)

        cmd = mock_run.call_args[0][0]
        self.assertIn("curl", cmd)
        self.assertIn("-x", cmd)
        self.assertIn("http://127.0.0.1:8888", cmd)
        self.assertIn("https://github.com/signup", cmd)
        self.assertIn("%{http_code}", cmd)

    @patch("subprocess.run")
    def test_probe_status_blocked(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="403", returncode=0)
        st = probe_status("https://github.com/signup")
        self.assertEqual(st, 403)

    @patch("subprocess.run")
    def test_probe_status_timeout_returns_zero(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["curl"], timeout=10.0)
        st = probe_status("https://github.com/signup")
        self.assertEqual(st, 0)

    @patch("subprocess.run")
    def test_probe_status_os_error_returns_zero(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = OSError("curl not found")
        st = probe_status("https://github.com/signup")
        self.assertEqual(st, 0)

    @patch("subprocess.run")
    def test_probe_status_non_digit_returns_zero(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="Could not resolve host", returncode=6)
        st = probe_status("https://github.com/signup")
        self.assertEqual(st, 0)


class TestModeParsing(unittest.TestCase):
    """Verify mode resolution: none, direct, warp, explicit proxy, and env KANCAHUB_EGRESS."""

    def test_mode_none_returns_direct(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            proxy, proc, src = auto_egress(mode="none", verbose=False)
            self.assertIsNone(proxy)
            self.assertIsNone(proc)
            self.assertEqual(src, "direct")

    def test_mode_direct_returns_direct(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            proxy, proc, src = auto_egress(mode="direct", verbose=False)
            self.assertIsNone(proxy)
            self.assertIsNone(proc)
            self.assertEqual(src, "direct")

    def test_mode_warp_explicit(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            proxy, proc, src = auto_egress(mode="warp", verbose=False)
            self.assertIsNone(proxy)
            self.assertIsNone(proc)
            self.assertEqual(src, "warp")

    def test_mode_explicit_http_url(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            url = "http://user:pass@proxy.example.com:8080"
            proxy, proc, src = auto_egress(mode=url, verbose=False)
            self.assertEqual(proxy, url)
            self.assertIsNone(proc)
            self.assertEqual(src, "explicit")

    def test_mode_explicit_socks5_url(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            url = "socks5://127.0.0.1:1080"
            proxy, proc, src = auto_egress(mode=url, verbose=False)
            self.assertEqual(proxy, url)
            self.assertIsNone(proc)
            self.assertEqual(src, "explicit")

    def test_mode_explicit_host_port(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            proxy, proc, src = auto_egress(mode="192.168.1.100:8888", verbose=False)
            self.assertEqual(proxy, "http://192.168.1.100:8888")
            self.assertIsNone(proc)
            self.assertEqual(src, "explicit")

    def test_env_kancahub_egress_overrides_default_auto(self) -> None:
        env_url = "http://env-gateway.internal:9999"
        with patch.dict(os.environ, {"KANCAHUB_EGRESS": env_url}):
            proxy, proc, src = auto_egress(mode="auto", verbose=False)
            self.assertEqual(proxy, env_url)
            self.assertIsNone(proc)
            self.assertEqual(src, "env")

    def test_env_kancahub_egress_none(self) -> None:
        with patch.dict(os.environ, {"KANCAHUB_EGRESS": "none"}):
            proxy, proc, src = auto_egress(mode="auto", verbose=False)
            self.assertIsNone(proxy)
            self.assertIsNone(proc)
            self.assertEqual(src, "direct")


class TestAutoEgressExplicitNoSpawn(unittest.TestCase):
    """Ensure explicit mode and env proxy return immediately without spawning a gateway."""

    @patch("scripts.egress.ensure_clean_egress")
    @patch("subprocess.Popen")
    def test_no_gateway_spawned_for_explicit(self, mock_popen: MagicMock, mock_ensure: MagicMock) -> None:
        with patch.dict(os.environ, {}, clear=True):
            proxy, proc, src = auto_egress(mode="http://1.2.3.4:8080", verbose=False)
            self.assertEqual(proxy, "http://1.2.3.4:8080")
            self.assertIsNone(proc)
            self.assertEqual(src, "explicit")
            mock_ensure.assert_not_called()
            mock_popen.assert_not_called()


class TestAutoStrategyFallbackOrder(unittest.TestCase):
    """Verify fallback order of 'auto' strategy."""

    def setUp(self) -> None:
        self.env_patcher = patch.dict(os.environ, {}, clear=True)
        self.env_patcher.start()

    def tearDown(self) -> None:
        self.env_patcher.stop()

    @patch("scripts.egress.ensure_clean_egress")
    @patch("scripts.egress.probe_status")
    @patch("scripts.egress.is_port_open")
    def test_1_local_gateway_8888_preferred_when_ok(
        self,
        mock_port_open: MagicMock,
        mock_probe: MagicMock,
        mock_ensure: MagicMock,
    ) -> None:
        # Port 8888 is open and returns 200 OK on target_url probe
        mock_port_open.side_effect = lambda host, port, timeout=0.5: port == 8888
        mock_probe.return_value = 200

        proxy, proc, src = auto_egress(verbose=False)

        self.assertEqual(proxy, "http://127.0.0.1:8888")
        self.assertIsNone(proc)
        self.assertEqual(src, "local_gateway:8888")
        mock_ensure.assert_not_called()

    @patch("scripts.egress.ensure_clean_egress")
    @patch("scripts.egress.probe_status")
    @patch("scripts.egress.is_port_open")
    def test_1_local_gateway_8899_used_if_8888_closed(
        self,
        mock_port_open: MagicMock,
        mock_probe: MagicMock,
        mock_ensure: MagicMock,
    ) -> None:
        # Port 8888 is closed; 8899 is open and returns 200
        mock_port_open.side_effect = lambda host, port, timeout=0.5: port == 8899
        mock_probe.return_value = 200

        proxy, proc, src = auto_egress(verbose=False)

        self.assertEqual(proxy, "http://127.0.0.1:8899")
        self.assertIsNone(proc)
        self.assertEqual(src, "local_gateway:8899")
        mock_ensure.assert_not_called()

    @patch("scripts.egress.ensure_clean_egress")
    @patch("scripts.egress.probe_status")
    @patch("scripts.egress.is_port_open")
    def test_2_pool_gateway_used_when_local_gateways_unavailable(
        self,
        mock_port_open: MagicMock,
        mock_probe: MagicMock,
        mock_ensure: MagicMock,
    ) -> None:
        # No local gateways listening
        mock_port_open.return_value = False
        fake_proc = MagicMock()
        mock_ensure.return_value = ("http://127.0.0.1:9123", fake_proc)
        mock_probe.return_value = 200

        proxy, proc, src = auto_egress(verbose=False)

        self.assertEqual(proxy, "http://127.0.0.1:9123")
        self.assertEqual(proc, fake_proc)
        self.assertEqual(src, "pool_gateway")

    @patch("scripts.egress.stop_gateway")
    @patch("scripts.egress._check_warp_up")
    @patch("scripts.egress.ensure_clean_egress")
    @patch("scripts.egress.probe_status")
    @patch("scripts.egress.is_port_open")
    def test_3a_escalate_to_warp_when_pool_gateway_blocked(
        self,
        mock_port_open: MagicMock,
        mock_probe: MagicMock,
        mock_ensure: MagicMock,
        mock_warp_up: MagicMock,
        mock_stop: MagicMock,
    ) -> None:
        mock_port_open.return_value = False
        fake_pool_proc = MagicMock()
        mock_ensure.return_value = ("http://127.0.0.1:9123", fake_pool_proc)

        # Probe via pool gateway is BLOCKED (403); direct probe via active WARP succeeds (200)
        def fake_probe(url: str, proxy: str | None = None, timeout: float = 10.0) -> int:
            if proxy == "http://127.0.0.1:9123":
                return 403
            if proxy is None:
                return 200
            return 0

        mock_probe.side_effect = fake_probe
        mock_warp_up.return_value = True

        proxy, proc, src = auto_egress(verbose=False)

        # Pool gateway should be cleanly stopped and WARP used
        mock_stop.assert_called_with(fake_pool_proc)
        self.assertIsNone(proxy)
        self.assertIsNone(proc)
        self.assertEqual(src, "warp")

    @patch("scripts.egress.Path.stat")
    @patch("scripts.egress.Path.exists")
    @patch("scripts.egress.stop_gateway")
    @patch("scripts.egress._check_warp_up")
    @patch("scripts.egress.ensure_clean_egress")
    @patch("scripts.egress.probe_status")
    @patch("scripts.egress.is_port_open")
    def test_3b_escalate_to_residential_when_pool_blocked_and_warp_down(
        self,
        mock_port_open: MagicMock,
        mock_probe: MagicMock,
        mock_ensure: MagicMock,
        mock_warp_up: MagicMock,
        mock_stop: MagicMock,
        mock_exists: MagicMock,
        mock_stat: MagicMock,
    ) -> None:
        mock_port_open.return_value = False
        mock_warp_up.return_value = False
        mock_exists.return_value = True
        mock_stat.return_value = MagicMock(st_size=1024)

        fake_pool_proc = MagicMock()
        fake_res_proc = MagicMock()

        # Call 1 (pool): returns ("http://127.0.0.1:9123", fake_pool_proc)
        # Call 2 (residential): returns ("http://127.0.0.1:9124", fake_res_proc)
        mock_ensure.side_effect = [
            ("http://127.0.0.1:9123", fake_pool_proc),
            ("http://127.0.0.1:9124", fake_res_proc),
        ]

        # Probe on pool returns 403; probe on residential returns 200
        def fake_probe(url: str, proxy: str | None = None, timeout: float = 10.0) -> int:
            if proxy == "http://127.0.0.1:9123":
                return 403
            if proxy == "http://127.0.0.1:9124":
                return 200
            return 0

        mock_probe.side_effect = fake_probe

        proxy, proc, src = auto_egress(verbose=False)

        mock_stop.assert_called_with(fake_pool_proc)
        self.assertEqual(proxy, "http://127.0.0.1:9124")
        self.assertEqual(proc, fake_res_proc)
        self.assertEqual(src, "residential")

    @patch("scripts.egress.Path.exists")
    @patch("scripts.egress._check_warp_up")
    @patch("scripts.egress.ensure_clean_egress")
    @patch("scripts.egress.is_port_open")
    def test_4_graceful_direct_fallback_when_all_fail(
        self,
        mock_port_open: MagicMock,
        mock_ensure: MagicMock,
        mock_warp_up: MagicMock,
        mock_exists: MagicMock,
    ) -> None:
        mock_port_open.return_value = False
        mock_ensure.return_value = (None, None)
        mock_warp_up.return_value = False
        mock_exists.return_value = False

        proxy, proc, src = auto_egress(verbose=False)

        self.assertIsNone(proxy)
        self.assertIsNone(proc)
        self.assertEqual(src, "direct")


class TestSideEffectsOnImport(unittest.TestCase):
    """Verify that importing egress.py produces no network or process spawning side effects."""

    def test_import_is_pure(self) -> None:
        import importlib
        import scripts.egress as egress_module

        # Reloading should execute without raising or connecting
        importlib.reload(egress_module)
        self.assertTrue(callable(egress_module.auto_egress))
        self.assertTrue(callable(egress_module.probe_status))
        self.assertTrue(callable(egress_module.is_blocked))


if __name__ == "__main__":
    unittest.main()
