"""
tests/test_thk_vpngate_rotation.py — per-account relay rotation for the THK farm.

WHY THIS EXISTS
---------------
THK caps signups per network (measured 2026-10-19: a fresh relay accepted a
signup, the next attempt on the same relay returned the generic anti-abuse
error). The farm's chunk loop used to stop on the first capped chunk, so a batch
of 4 produced 1-2 accounts and then quit. `_thk_vpngate_rotate` is what lets it
continue on a new relay; these tests pin that contract without any network:

  - a relay whose exit IP is new is accepted and recorded;
  - a relay that reports an already-used exit IP is torn down and retried;
  - a relay that will not come up is skipped;
  - giving up after `tries` returns False so the caller stops (never loops forever).

Also pins the loop's step construction for a tunnel egress: `proxy verify` has
no proxy URL to probe for a system-wide tunnel, so it must be omitted — it
exited 1 and aborted the whole run before this.
"""

from __future__ import annotations

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import vpngate  # noqa: E402
import commands_thk  # noqa: E402
import r9_farm_loop  # noqa: E402


class TestVpngateRotate(unittest.TestCase):
    def setUp(self) -> None:
        self.ups: list[int] = []
        self.downs = 0

    def _patch(self, ips: list[str | None], up_rc: list[int] | None = None):
        """`ips` is the exit IP reported on each successive SUCCESSFUL up()."""
        rcs = list(up_rc) if up_rc else None
        counter = {"attempts": 0, "successes": 0}

        def fake_up(country, index):
            self.ups.append(index)
            counter["attempts"] += 1
            if rcs is None:
                return 0
            n = counter["attempts"] - 1
            return rcs[n] if n < len(rcs) else 1

        def fake_exit_ip():
            counter["successes"] += 1
            n = counter["successes"] - 1
            return ips[n] if n < len(ips) else None

        def fake_down():
            self.downs += 1

        return [
            mock.patch.object(vpngate, "up", side_effect=fake_up),
            mock.patch.object(vpngate, "exit_ip", side_effect=fake_exit_ip),
            mock.patch.object(vpngate, "down", side_effect=fake_down),
        ]

    def test_accepts_a_fresh_exit_ip(self) -> None:
        used = {"161.120.177.213"}
        state: dict = {}
        for p in self._patch(["112.68.148.47"]):
            p.start(); self.addCleanup(p.stop)
        self.assertTrue(commands_thk._thk_vpngate_rotate("JP", used, state))
        self.assertIn("112.68.148.47", used)
        self.assertEqual(state["index"], 1)

    def test_rejects_an_already_used_relay_and_advances(self) -> None:
        used = {"1.1.1.1"}
        state: dict = {}
        # first attempt repeats the used IP, second is new
        for p in self._patch(["1.1.1.1", "2.2.2.2"]):
            p.start(); self.addCleanup(p.stop)
        self.assertTrue(commands_thk._thk_vpngate_rotate("JP", used, state))
        self.assertIn("2.2.2.2", used)
        self.assertEqual(len(self.ups), 2, "should have retried a different relay")
        self.assertGreaterEqual(self.downs, 1, "stale relay must be torn down")

    def test_skips_a_relay_that_will_not_come_up(self) -> None:
        used: set[str] = set()
        state: dict = {}
        for p in self._patch(["3.3.3.3"], up_rc=[1, 0]):
            p.start(); self.addCleanup(p.stop)
        self.assertTrue(commands_thk._thk_vpngate_rotate("JP", used, state))
        self.assertEqual(state["index"], 2)

    def test_gives_up_after_tries(self) -> None:
        used = {"9.9.9.9"}
        state: dict = {}
        for p in self._patch(["9.9.9.9"] * 3):
            p.start(); self.addCleanup(p.stop)
        self.assertFalse(commands_thk._thk_vpngate_rotate("JP", used, state, tries=3))
        self.assertEqual(len(self.ups), 3, "must not retry past the budget")


class TestRunStepsForTunnel(unittest.TestCase):
    def _plan(self, proxy, per_ip=1, n=4, farm_only=False) -> str:
        args = mock.Mock(proxy=proxy, per_ip=per_ip, max=n, apply=False, farm_only=farm_only)
        buf = io.StringIO()
        with redirect_stdout(buf):
            r9_farm_loop.cmd_run(args)
        return buf.getvalue()

    def test_tunnel_skips_proxy_verify(self) -> None:
        out = self._plan("vpngate:JP")
        steps = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("kancahub ")]
        self.assertFalse([s for s in steps if s.startswith("kancahub proxy verify")],
                         f"tunnel must not run proxy verify; steps were {steps}")
        self.assertIn("skipping proxy verify", out)

    def test_invocator_still_injects(self) -> None:
        out = self._plan("vpngate:JP")
        self.assertIn("kancahub thk inject --verify", out)
        self.assertIn("kancahub thk sync --prune", out)

    def test_farm_only_host_skips_inject_and_sync(self) -> None:
        """The farm box has no 9Router DB; both steps failed there every run."""
        out = self._plan("vpngate:JP", farm_only=True)
        self.assertNotIn("kancahub thk inject", out)
        self.assertNotIn("kancahub thk sync", out)

    def test_tunnel_batch_carries_egress_and_per_ip(self) -> None:
        out = self._plan("vpngate:JP", per_ip=1)
        self.assertIn("thk batch 4 --proxy vpngate:JP --per-ip 1", out)

    def test_proxy_url_still_verifies(self) -> None:
        out = self._plan("http://127.0.0.1:8888")
        self.assertIn("proxy verify --proxy http://127.0.0.1:8888", out)
        self.assertNotIn("skipping proxy verify", out)


if __name__ == "__main__":
    unittest.main()