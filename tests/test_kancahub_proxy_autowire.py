"""Unit tests for kancahub's egress auto-wire into the farm commands.

No network, no gateway spawns: scripts/egress.auto_egress is always faked.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import kancahub
from scripts.kancahub import (
    EgressChoice,
    _choose_egress,
    _proxy_env,
    _proxy_flags,
    _resolve_proxy,
    build_parser,
)


class _FakeEgress:
    """Stand-in for scripts/egress.py that records its calls."""

    def __init__(self, result=("http://127.0.0.1:8888", None, "local_gateway:8888")):
        self.result = result
        self.calls: list[tuple[str, str]] = []
        self.stopped: list[object] = []

    def auto_egress(self, target_url: str, *, mode: str = "auto", verbose: bool = True):
        self.calls.append((target_url, mode))
        return self.result

    def stop_gateway(self, proc):
        self.stopped.append(proc)


class TestResolveProxyMapping(unittest.TestCase):
    """_resolve_proxy maps 'none' -> None, explicit URL -> URL, 'auto' -> egress result."""

    def test_none_maps_to_direct(self) -> None:
        with patch.object(kancahub, "_load_egress") as loader:
            self.assertIsNone(_resolve_proxy("none", "https://github.com/signup"))
            self.assertIsNone(_resolve_proxy("direct", "https://github.com/signup"))
            loader.assert_not_called()  # direct must never touch the egress helper

    def test_explicit_url_is_passed_through_unchanged(self) -> None:
        fake = _FakeEgress(("http://1.2.3.4:8080", None, "explicit"))
        with patch.object(kancahub, "_load_egress", return_value=fake):
            got = _resolve_proxy("http://1.2.3.4:8080", "https://github.com/signup")
        self.assertEqual(got, "http://1.2.3.4:8080")
        self.assertEqual(fake.calls, [("https://github.com/signup", "http://1.2.3.4:8080")])

    def test_scheme_less_url_gains_http_scheme(self) -> None:
        fake = _FakeEgress(("http://127.0.0.1:8899", None, "explicit"))
        with patch.object(kancahub, "_load_egress", return_value=fake):
            got = _resolve_proxy("127.0.0.1:8899", "https://github.com/signup")
        self.assertEqual(got, "http://127.0.0.1:8899")
        self.assertEqual(fake.calls[0][1], "http://127.0.0.1:8899")

    def test_auto_returns_whatever_auto_egress_yields(self) -> None:
        fake = _FakeEgress(("http://10.0.0.5:9999", None, "pool_gateway"))
        with patch.object(kancahub, "_load_egress", return_value=fake):
            got = _resolve_proxy("auto", "https://github.com/signup")
        self.assertEqual(got, "http://10.0.0.5:9999")
        self.assertEqual(fake.calls, [("https://github.com/signup", "auto")])

    def test_auto_direct_fallback_returns_none(self) -> None:
        fake = _FakeEgress((None, None, "direct"))
        with patch.object(kancahub, "_load_egress", return_value=fake):
            self.assertIsNone(_resolve_proxy("auto", "https://github.com/signup"))

    def test_warp_returns_none_but_is_reported_as_warp(self) -> None:
        fake = _FakeEgress((None, None, "warp"))
        with patch.object(kancahub, "_load_egress", return_value=fake):
            choice = _choose_egress("warp", "https://github.com/signup")
        self.assertIsNone(choice.proxy)
        self.assertTrue(choice.warp)

    def test_spawned_gateway_is_stopped_by_teardown(self) -> None:
        sentinel = object()
        fake = _FakeEgress(("http://127.0.0.1:8888", sentinel, "pool_gateway"))
        with patch.object(kancahub, "_load_egress", return_value=fake):
            kancahub._AUTO_GATEWAYS.clear()
            _choose_egress("auto", "https://github.com/signup")
            self.assertEqual(kancahub._AUTO_GATEWAYS, [sentinel])
            kancahub._stop_auto_gateways()
        self.assertEqual(fake.stopped, [sentinel])
        self.assertEqual(kancahub._AUTO_GATEWAYS, [])


class TestGracefulFallback(unittest.TestCase):
    """A missing/broken egress helper degrades to the legacy behavior."""

    def test_import_failure_auto_yields_unavailable_not_a_crash(self) -> None:
        with patch.object(kancahub, "_load_egress", return_value=None):
            choice = _choose_egress("auto", "https://github.com/signup")
        self.assertTrue(choice.unavailable)
        self.assertIsNone(choice.proxy)

    def test_import_failure_still_honours_an_explicit_url(self) -> None:
        with patch.object(kancahub, "_load_egress", return_value=None):
            choice = _choose_egress("http://5.6.7.8:3128", "https://github.com/signup")
        self.assertEqual(choice.proxy, "http://5.6.7.8:3128")
        self.assertFalse(choice.unavailable)
        self.assertEqual(_proxy_flags(choice, supports_no_proxy=True),
                         ["--proxy", "http://5.6.7.8:3128"])

    def test_auto_egress_raising_degrades_to_unavailable(self) -> None:
        class _Broken:
            def auto_egress(self, *a, **kw):
                raise RuntimeError("probe exploded")

        with patch.object(kancahub, "_load_egress", return_value=_Broken()):
            choice = _choose_egress("auto", "https://github.com/signup")
        self.assertTrue(choice.unavailable)
        self.assertIsNone(choice.proxy)
        # unavailable must not add --no-proxy: the child keeps its own default
        self.assertEqual(_proxy_flags(choice, supports_no_proxy=True), [])


class TestProxyFlagsAndEnv(unittest.TestCase):
    """Flag/env translation for children with and without a --proxy option."""

    def test_proxy_flag_for_url_choice(self) -> None:
        choice = EgressChoice("http://127.0.0.1:8888", "auto:local_gateway:8888")
        self.assertEqual(_proxy_flags(choice, supports_no_proxy=True),
                         ["--proxy", "http://127.0.0.1:8888"])
        self.assertEqual(_proxy_env(choice)["HTTPS_PROXY"], "http://127.0.0.1:8888")

    def test_no_proxy_flag_for_direct_choice(self) -> None:
        choice = EgressChoice(None, "direct", direct=True)
        self.assertEqual(_proxy_flags(choice, supports_no_proxy=True), ["--no-proxy"])
        self.assertIsNone(_proxy_env(choice))

    def test_no_proxy_flag_omitted_when_child_lacks_it(self) -> None:
        choice = EgressChoice(None, "none", direct=True)
        self.assertEqual(_proxy_flags(choice, supports_no_proxy=False), [])
        self.assertIsNone(_proxy_env(choice))


class TestArgparsing(unittest.TestCase):
    """The new --proxy option must parse on the farm commands."""

    def setUp(self) -> None:
        self.parser = build_parser()

    def test_github_farm_proxy(self) -> None:
        args = self.parser.parse_args(["github", "farm"])
        self.assertEqual(args.proxy, "auto")
        args = self.parser.parse_args(["github", "farm", "--proxy", "none"])
        self.assertEqual(args.proxy, "none")
        args = self.parser.parse_args(
            ["github", "farm", "--proxy", "http://127.0.0.1:8888"])
        self.assertEqual(args.proxy, "http://127.0.0.1:8888")

    def test_grok_run_and_inject_proxy(self) -> None:
        self.assertEqual(self.parser.parse_args(["grok", "run"]).proxy, "auto")
        self.assertEqual(
            self.parser.parse_args(["grok", "run", "--proxy", "warp"]).proxy, "warp")
        self.assertEqual(self.parser.parse_args(["grok", "inject"]).proxy, "auto")
        self.assertEqual(
            self.parser.parse_args(["grok", "inject", "--proxy", "none"]).proxy, "none")

    def test_thk_k12_and_github_verify_proxy(self) -> None:
        self.assertEqual(self.parser.parse_args(["thk", "batch"]).proxy, "auto")
        self.assertEqual(
            self.parser.parse_args(["thk", "batch", "--proxy", "warp"]).proxy, "warp")
        self.assertEqual(self.parser.parse_args(["k12", "auto"]).proxy, "auto")
        self.assertEqual(
            self.parser.parse_args(["k12", "verify", "http://x/"]).proxy, "auto")

    def test_parsing_never_resolves_the_proxy(self) -> None:
        """--help/parse must stay offline: auto_egress is not called at parse time."""
        fake = _FakeEgress()
        with patch.object(kancahub, "_load_egress", return_value=fake):
            self.parser.parse_args(["github", "farm"])
            self.parser.parse_args(["grok", "run", "--proxy", "auto"])
            self.parser.parse_args(["k12", "auto"])
        self.assertEqual(fake.calls, [])

    def test_existing_flags_still_parse(self) -> None:
        args = self.parser.parse_args(
            ["github", "farm", "--domain", "myid", "--max-accounts", "3",
             "--headless", "--dry-run", "--pool", "p.txt", "--retries", "2"])
        self.assertEqual(args.domain, "myid")
        self.assertEqual(args.max_accounts, 3)
        self.assertTrue(args.headless and args.dry_run)
        self.assertEqual(args.pool, "p.txt")
        self.assertEqual(args.retries, 2)


class TestGithubFarmMapping(unittest.TestCase):
    """map_github_farm_args forwards the resolved hop, not the raw mode."""

    def _args(self, **kw):
        base = dict(index=1, domain="binus", inbox=None, max_accounts=None,
                    delay_min=None, delay_max=None, retries=0, proxy="auto",
                    pool=None, headless=False, dry_run=False, no_proxy=False)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_auto_gateway_is_forwarded_as_proxy(self) -> None:
        choice = EgressChoice("http://127.0.0.1:8888", "auto:local_gateway:8888")
        cmd = kancahub.map_github_farm_args(self._args(), choice)
        self.assertIn("--proxy", cmd)
        self.assertEqual(cmd[cmd.index("--proxy") + 1], "http://127.0.0.1:8888")
        self.assertNotIn("--no-proxy", cmd)

    def test_direct_choice_forces_no_proxy(self) -> None:
        cmd = kancahub.map_github_farm_args(
            self._args(proxy="none"), EgressChoice(None, "none", direct=True))
        self.assertIn("--no-proxy", cmd)
        self.assertNotIn("--proxy", cmd)

    def test_explicit_url_wins_over_pool(self) -> None:
        choice = EgressChoice("http://127.0.0.1:8888", "explicit")
        cmd = kancahub.map_github_farm_args(self._args(pool="pool.txt"), choice)
        self.assertIn("--proxy", cmd)
        self.assertNotIn("--pool", cmd)  # verified hop beats pool rotation

    def test_pool_is_kept_when_no_proxy_is_resolved(self) -> None:
        choice = EgressChoice(None, "unavailable", unavailable=True)
        cmd = kancahub.map_github_farm_args(self._args(pool="pool.txt"), choice)
        self.assertIn("--pool", cmd)
        self.assertNotIn("--proxy", cmd)
        self.assertNotIn("--no-proxy", cmd)  # legacy behavior preserved

    def test_mapper_never_probes_the_network(self) -> None:
        """Calling the mapper without a resolved choice must stay side-effect free.

        Regression guard: an earlier version resolved 'auto' inside the mapper,
        which made offline callers start real gateways and hit github.com.
        """
        def _explode():
            raise AssertionError("mapper must not load the egress helper")

        with patch.object(kancahub, "_load_egress", side_effect=_explode):
            cmd = kancahub.map_github_farm_args(self._args(proxy="auto"))
        self.assertNotIn("--proxy", cmd)
        self.assertNotIn("--no-proxy", cmd)

    def test_mapper_forwards_an_explicit_url_without_probing(self) -> None:
        def _explode():
            raise AssertionError("explicit URL needs no probing")

        with patch.object(kancahub, "_load_egress", side_effect=_explode):
            cmd = kancahub.map_github_farm_args(
                self._args(proxy="http://9.9.9.9:3128"))
        self.assertEqual(cmd[cmd.index("--proxy") + 1], "http://9.9.9.9:3128")

    def test_mapper_honours_the_no_proxy_alias(self) -> None:
        cmd = kancahub.map_github_farm_args(self._args(no_proxy=True))
        self.assertIn("--no-proxy", cmd)


class TestLazyResolution(unittest.TestCase):
    """FARM commands resolve the egress at run time, and only once."""

    def test_github_farm_resolves_once_at_run_time(self) -> None:
        fake = _FakeEgress(("http://127.0.0.1:8888", None, "local_gateway:8888"))
        parser = build_parser()
        args = parser.parse_args(["github", "farm", "--dry-run"])
        with patch.object(kancahub, "_load_egress", return_value=fake), \
                patch.object(kancahub, "run", lambda cmd, cwd=None, env=None: 0):
            rc = kancahub.cmd_github(args)
        self.assertEqual(rc, 0)
        self.assertEqual(len(fake.calls), 1)
        self.assertEqual(fake.calls[0][1], "auto")

    def test_pool_wins_only_when_no_hop_resolved(self) -> None:
        """With --pool and an explicit URL, the verified hop wins."""
        fake = _FakeEgress(("http://127.0.0.1:8888", None, "local_gateway:8888"))
        parser = build_parser()
        args = parser.parse_args(["github", "farm", "--pool", "p.txt"])
        captured: list[list[str]] = []
        with patch.object(kancahub, "_load_egress", return_value=fake), \
                patch.object(kancahub, "run",
                             lambda cmd, cwd=None, env=None: captured.append(cmd) or 0):
            kancahub.cmd_github(args)
        self.assertIn("--proxy", captured[0])
        self.assertNotIn("--pool", captured[0])


class TestNoProxyAndEnvInheritance(unittest.TestCase):
    """'none' forces direct everywhere; children without --proxy inherit via env."""

    def _capture(self, argv, choice):
        captured: list[tuple[list[str], dict | None]] = []
        parser = build_parser()
        with patch.object(kancahub, "_choose_egress", return_value=choice) as ch, \
                patch.object(kancahub, "run",
                             lambda cmd, cwd=None, env=None: captured.append((cmd, env)) or 0), \
                patch.object(kancahub, "_stop_auto_gateways"):
            rc = kancahub.dispatch(parser, parser.parse_args(argv))
        return rc, captured[0], ch.call_args[0][0]

    def test_thk_batch_none_is_direct_and_unsets_env(self) -> None:
        _, (cmd, env), mode = self._capture(
            ["thk", "batch", "2", "--proxy", "none"],
            EgressChoice(None, "none", direct=True))
        self.assertEqual(mode, "none")
        self.assertEqual(env, {"TOKENHARBOR_NO_PROXY": "1"})

    def test_thk_batch_auto_inherits_through_env(self) -> None:
        _, (cmd, env), mode = self._capture(
            ["thk", "batch", "2"],
            EgressChoice("http://127.0.0.1:8888", "auto:local_gateway:8888"))
        self.assertEqual(mode, "auto")
        self.assertEqual(env["HTTPS_PROXY"], "http://127.0.0.1:8888")

    def test_k12_auto_no_proxy_alias_forces_direct(self) -> None:
        _, (cmd, env), mode = self._capture(
            ["k12", "auto", "--no-proxy"], EgressChoice(None, "none", direct=True))
        self.assertEqual(mode, "none")
        self.assertIsNone(env)

    def test_grok_run_no_proxy_alias_forces_direct(self) -> None:
        _, (cmd, env), mode = self._capture(
            ["grok", "run", "--no-proxy"], EgressChoice(None, "none", direct=True))
        self.assertEqual(mode, "none")
        self.assertNotIn("--proxy", cmd)

    def test_k12_verify_gateway_uses_explicit_url(self) -> None:
        _, (cmd, env), mode = self._capture(
            ["k12", "verify", "https://services.sheerid.com/verify/x/", "--gateway"],
            EgressChoice("http://127.0.0.1:8888", "explicit"))
        self.assertEqual(mode, "127.0.0.1:8888")
        self.assertEqual(cmd[cmd.index("--proxy") + 1], "http://127.0.0.1:8888")


if __name__ == "__main__":
    unittest.main()