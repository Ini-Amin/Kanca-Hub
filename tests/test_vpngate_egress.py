"""
tests/test_vpngate_egress.py — VPN Gate relay-list fetch and exit-IP probing.

WHY THIS EXISTS
---------------
The farm box's direct route to vpngate.net returns HTTP 200 with a 636-byte
interception page (measured 2026-10-19), so `relays()` yielded nothing and the
farm printed "no relays" — it could never rotate IPs, which is exactly what
TokenHarbor's per-IP signup cap requires. Routing the fetch through the local
WARP proxy returns the real 1.3 MB CSV (verified: 100 relays).

Separately, `exit_ip()` used a single echo service; api.ipify.org answered
"Too Many Requests" from that box, so a perfectly good tunnel looked failed.
Both are pinned here.
"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import vpngate  # noqa: E402

CSV = """*vpn_servers
#HostName,IP,Score,Ping,Speed,CountryLong,CountryShort,NumVpnSessions,Uptime,TotalUsers,TotalTraffic,LogType,Operator,Message,OpenVPN_ConfigData_Base64
public-vpn-1,1.2.3.4,291,14,8000000,Japan,JP,5,100,10,20,2weeks,op,OK,QUJD
public-vpn-2,5.6.7.8,120,40,900000,Korea Republic of,KR,5,100,10,20,2weeks,op,OK,REVG
"""


class _FakeOpener:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.used = 0

    def open(self, url, timeout=None):  # noqa: A003 - mirrors urllib API
        self.used += 1
        return io.BytesIO(self.payload)


class TestApiProxySelection(unittest.TestCase):
    def test_env_var_wins(self) -> None:
        with mock.patch.dict("os.environ", {vpngate.API_PROXY_ENV: "http://p:1"}), \
             mock.patch.object(vpngate.socket, "create_connection",
                               side_effect=AssertionError("must not probe when env set")):
            self.assertEqual(vpngate._api_proxy(), "http://p:1")

    def test_autodetects_local_warp_proxy(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch.object(vpngate.socket, "create_connection", return_value=mock.MagicMock()):
            self.assertEqual(vpngate._api_proxy(), vpngate.WARP_PROXY)

    def test_none_when_nothing_listens(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch.object(vpngate.socket, "create_connection", side_effect=OSError("refused")):
            self.assertIsNone(vpngate._api_proxy())

    def test_opener_uses_proxy_handler(self) -> None:
        captured = {}

        def fake_build(*handlers):
            captured["handlers"] = handlers
            return mock.MagicMock()

        with mock.patch.object(vpngate, "_api_proxy", return_value="http://p:1"), \
             mock.patch.object(vpngate.urllib.request, "build_opener", side_effect=fake_build):
            vpngate._api_opener()
        handler = captured["handlers"][0]
        self.assertIsInstance(handler, vpngate.urllib.request.ProxyHandler)
        self.assertEqual(handler.proxies["https"], "http://p:1")


class TestRelayCache(unittest.TestCase):
    def setUp(self) -> None:
        vpngate._RELAY_CACHE.clear()
        self.addCleanup(vpngate._RELAY_CACHE.clear)

    def test_second_call_does_not_refetch(self) -> None:
        opener = _FakeOpener(CSV.encode())
        with mock.patch.object(vpngate, "_api_opener", return_value=opener):
            first = vpngate.relays("JP")
            vpngate.relays("JP")
        self.assertEqual(opener.used, 1, "rotating batches must not re-fetch per relay")
        self.assertIs(first, vpngate.relays("JP"))

    def test_cache_is_per_country(self) -> None:
        opener = _FakeOpener(CSV.encode())
        with mock.patch.object(vpngate, "_api_opener", return_value=opener):
            vpngate.relays("JP")
            vpngate.relays("KR")
        self.assertEqual(opener.used, 2)


class TestApiProxyWithTunnel(unittest.TestCase):
    def test_tunnel_up_bypasses_the_warp_proxy(self) -> None:
        """A live tunnel is the egress; the WARP proxy path stalls then."""
        fake_pid = mock.MagicMock()
        fake_pid.read_text.return_value = "4242\n"
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch.object(vpngate, "PID", fake_pid), \
             mock.patch.object(vpngate.socket, "create_connection",
                               side_effect=AssertionError("must not probe WARP while tunnelled")):
            self.assertIsNone(vpngate._api_proxy())

    def test_tunnel_down_uses_warp_proxy(self) -> None:
        fake_pid = mock.MagicMock()
        fake_pid.read_text.return_value = ""
        with mock.patch.dict("os.environ", {}, clear=True), \
             mock.patch.object(vpngate, "PID", fake_pid), \
             mock.patch.object(vpngate.socket, "create_connection", return_value=mock.MagicMock()):
            self.assertEqual(vpngate._api_proxy(), vpngate.WARP_PROXY)


class TestRelays(unittest.TestCase):
    def setUp(self) -> None:
        vpngate._RELAY_CACHE.clear()
        self.addCleanup(vpngate._RELAY_CACHE.clear)

    def test_parses_and_sorts_by_speed(self) -> None:
        opener = _FakeOpener(CSV.encode())
        with mock.patch.object(vpngate, "_api_opener", return_value=opener):
            rs = vpngate.relays()
        self.assertEqual([r["IP"] for r in rs], ["1.2.3.4", "5.6.7.8"])  # 8e6 before 9e5

    def test_country_filter(self) -> None:
        with mock.patch.object(vpngate, "_api_opener", return_value=_FakeOpener(CSV.encode())):
            self.assertEqual([r["IP"] for r in vpngate.relays("JP")], ["1.2.3.4"])
            self.assertEqual(vpngate.relays("ZZ"), [])

    def test_rows_without_config_are_dropped(self) -> None:
        csv_text = CSV + "public-vpn-3,9.9.9.9,1,1,1,Nowhere,ZZ,0,0,0,0,x,op,OK,\n"
        with mock.patch.object(vpngate, "_api_opener", return_value=_FakeOpener(csv_text.encode())):
            self.assertNotIn("9.9.9.9", [r["IP"] for r in vpngate.relays()])


class TestExitIpFallback(unittest.TestCase):
    def test_skips_ratelimit_body_and_uses_next_echo(self) -> None:
        bodies = {"https://api.ipify.org": '{"error": "Too Many Requests"}',
                  "https://ifconfig.me/ip": "203.0.113.7"}

        def fake_urlopen(url, timeout=None):
            return io.BytesIO(bodies[url].encode())

        with mock.patch.object(vpngate.urllib.request, "urlopen", side_effect=fake_urlopen):
            self.assertEqual(vpngate.exit_ip(), "203.0.113.7")

    def test_returns_none_when_all_echoes_fail(self) -> None:
        with mock.patch.object(vpngate.urllib.request, "urlopen", side_effect=OSError("down")):
            self.assertIsNone(vpngate.exit_ip())

    def test_rejects_non_ip_payload(self) -> None:
        with mock.patch.object(vpngate.urllib.request, "urlopen",
                               return_value=io.BytesIO(b"<html>nothing</html>")):
            self.assertIsNone(vpngate.exit_ip())


if __name__ == "__main__":
    unittest.main()