import sys
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import fastly_serverlist as fs


class BaseCacheTest(unittest.TestCase):
    """Common setup: redirect cache paths to a temp dir."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tmp_path = Path(tmp.name)
        cache_dir = tmp_path / "cfg"
        cache_file = cache_dir / "vpn_serverlist.json"
        p1 = patch.object(fs, "CACHE_DIR", cache_dir)
        p2 = patch.object(fs, "CACHE_FILE", cache_file)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)


def _fake_urlopen(payload_bytes):
    """Return an urlopen replacement yielding payload_bytes as response body."""
    resp = MagicMock()
    resp.read.return_value = payload_bytes
    resp.__enter__.return_value = resp
    resp.context = {}
    return MagicMock(return_value=resp)


RECORDS = [
    {
        "code": "us",
        "name": "United States",
        "cities": [
            {
                "code": "nyc",
                "name": "New York",
                "servers": [
                    {"hostname": "ny1.example.com", "port": 1234},
                    {"hostname": "ny2.example.com"},  # port omitted -> default
                    {"port": 9999},  # no hostname -> skipped
                ],
            },
        ],
    },
    {
        "code": "de",
        "name": "Germany",
        "cities": [
            {
                "code": "ber",
                "name": "Berlin",
                "servers": [{"hostname": "ber1.example.com", "port": 2499}],
            },
        ],
    },
]


class TestFetchServerlist(BaseCacheTest):

    def test_fetch_network_and_cache_write(self):
        body = json.dumps({"data": RECORDS}).encode()
        with patch.object(fs.urllib.request, "urlopen",
                          _fake_urlopen(body)) as mock_urlopen:
            result = fs.fetch_serverlist()

        self.assertEqual(result, RECORDS)
        self.assertEqual(mock_urlopen.call_count, 1)
        args, kwargs = mock_urlopen.call_args
        req = args[0]
        self.assertEqual(kwargs.get("timeout"), 10)
        self.assertIn("records", str(req.full_url))
        self.assertTrue(fs.CACHE_FILE.exists(),
                        "Result should have been written to cache")
        cached = json.loads(fs.CACHE_FILE.read_text())
        self.assertEqual(cached, RECORDS)

    def test_cache_hit_skips_network(self):
        fs.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fs.CACHE_FILE.write_text(json.dumps(RECORDS))

        with patch.object(fs.urllib.request, "urlopen",
                          _fake_urlopen(b'{"data": []}')) as mock_urlopen:
            result = fs.fetch_serverlist()

        mock_urlopen.assert_not_called()
        self.assertEqual(result, RECORDS)

    def test_stale_cache_refetches(self):
        fs.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fs.CACHE_FILE.write_text(json.dumps([{"old": True}]))

        with patch.object(fs, "CACHE_TTL", -1):  # force staleness
            with patch.object(fs.urllib.request, "urlopen",
                              _fake_urlopen(b'{"data": [{"new": true}]}')):
                result = fs.fetch_serverlist()
        self.assertEqual(result, [{"new": True}])

    def test_force_refresh_ignores_fresh_cache(self):
        fs.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fs.CACHE_FILE.write_text(json.dumps([{"cached": True}]))

        with patch.object(fs.urllib.request, "urlopen",
                          _fake_urlopen(b'{"data": [{"fresh": true}]}')):
            result = fs.fetch_serverlist(force_refresh=True)
        self.assertEqual(result, [{"fresh": True}])

    def test_corrupt_cache_falls_back_to_network(self):
        fs.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fs.CACHE_FILE.write_text("{not valid json")

        with patch.object(fs.urllib.request, "urlopen",
                          _fake_urlopen(b'{"data": [{"ok": true}]}')):
            result = fs.fetch_serverlist()
        self.assertEqual(result, [{"ok": True}])

    def test_empty_cached_list_triggers_refetch(self):
        fs.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fs.CACHE_FILE.write_text(json.dumps([]))

        with patch.object(fs.urllib.request, "urlopen",
                          _fake_urlopen(b'{"data": [{"x": 1}]}')) as mock_u:
            result = fs.fetch_serverlist()
        mock_u.assert_called_once()
        self.assertEqual(result, [{"x": 1}])

    def test_missing_data_key_yields_empty_list(self):
        with patch.object(fs.urllib.request, "urlopen",
                          _fake_urlopen(b'{}')):
            result = fs.fetch_serverlist()
        self.assertEqual(result, [])


class TestParseServers(unittest.TestCase):

    def test_flattens_records_with_defaults(self):
        out = fs.parse_servers(RECORDS)
        ny1, ny2, ber = out
        self.assertEqual(len(out), 3)
        self.assertEqual(ny1, {
            "country_code": "us",
            "country_name": "United States",
            "city_code": "nyc",
            "city_name": "New York",
            "host": "ny1.example.com",
            "port": 1234,
        })
        # Missing port falls back to 2499
        self.assertEqual(ny2["port"], 2499)
        self.assertEqual(ny2["host"], "ny2.example.com")
        # Second record parsed correctly
        self.assertEqual(ber["country_code"], "de")
        self.assertEqual(ber["city_name"], "Berlin")

    def test_skips_servers_without_hostname(self):
        out = fs.parse_servers(RECORDS)
        hosts = [s["host"] for s in out]
        self.assertNotIn(None, hosts)
        self.assertEqual(len(hosts), 3)

    def test_city_name_defaults_to_country(self):
        recs = [{
            "code": "fr",
            "name": "France",
            "cities": [{
                "code": "par",
                "servers": [{"hostname": "p.example.com", "port": 1}],
            }],
        }]
        out = fs.parse_servers(recs)
        self.assertEqual(out[0]["city_name"], "France")

    def test_empty_input(self):
        self.assertEqual(fs.parse_servers([]), [])
        self.assertEqual(fs.parse_servers([{}]), [])


class TestProbeLatency(unittest.TestCase):

    def _fake_conn_stack(self):
        conn = MagicMock()
        sock_ctx = MagicMock()
        sock_ctx.__enter__.return_value = conn
        sock_ctx.__exit__.return_value = False
        return conn, sock_ctx

    def test_success_returns_rounded_ms(self):
        _, sock_ctx = self._fake_conn_stack()
        ssock = MagicMock()
        ssock.__enter__.return_value = ssock
        ssock.__exit__.return_value = False
        ssock.cipher.return_value = ("AES", "TLS", 128)
        ctx = MagicMock()
        ctx.wrap_socket.return_value = ssock

        with patch.object(fs.socket, "create_connection", return_value=sock_ctx), \
             patch.object(fs.ssl, "create_default_context", return_value=ctx), \
             patch.object(fs.time, "perf_counter",
                          side_effect=[100.0, 101.234]):
            result = fs.probe_latency_ms("h", 443)

        self.assertIsInstance(result, float)
        self.assertAlmostEqual(result, round(1.234 * 1000.0, 2))

    def test_timeout_returns_none(self):
        _, sock_ctx = self._fake_conn_stack()

        def boom(a, b, timeout=None):
            raise OSError("timed out")

        with patch.object(fs.socket, "create_connection", side_effect=boom), \
             patch.object(fs.time, "perf_counter",
                          side_effect=[100.0]):
            result = fs.probe_latency_ms("h", 443, timeout=0.5)

        self.assertIsNone(result)

    def test_tls_failure_returns_none(self):
        _, sock_ctx = self._fake_conn_stack()
        ssock = MagicMock()
        ssock.__enter__.side_effect = ssl_error = ValueError("bad tls")
        ctx = MagicMock()
        ctx.wrap_socket.return_value = ssock

        with patch.object(fs.socket, "create_connection", return_value=sock_ctx), \
             patch.object(fs.ssl, "create_default_context", return_value=ctx), \
             patch.object(fs.time, "perf_counter",
                          side_effect=[100.0]):
            result = fs.probe_latency_ms("h", 443)

        self.assertIsNone(result)


class TestRankServers(BaseCacheTest):
    SERVERS = [
        {"country_code": "US", "country_name": "United States",
         "city_code": "nyc", "city_name": "New York",
         "host": f"h{i}.example.com", "port": 2499}
        for i in range(20)
    ]

    def setUp(self):
        super().setUp()
        self.records = [{"code": "US", "cities": []}]
        self.parsed = [dict(s) for s in self.SERVERS]

    def _run_rank(self, **kwargs):
        latencies = {}
        for i, s in enumerate(self.SERVERS):
            latencies[s["host"]] = float((i % 5) * 10)

        def fake_probes_get(host):
            return latencies.get(host)

        with patch.object(fs, "fetch_serverlist", return_value=self.records), \
             patch.object(fs, "parse_servers", return_value=self.parsed), \
             patch.object(fs, "probe_latency_ms",
                          side_effect=lambda h, p, timeout=3.0: latencies.get(h)):
            return fs.rank_servers(**kwargs)

    def test_sorted_by_latency(self):
        ranked = self._run_rank(max_candidates=len(self.SERVERS))
        self.assertEqual(len(ranked), len(self.SERVERS))
        lat = [r["latency_ms"] for r in ranked]
        self.assertEqual(lat, sorted(lat))
        hosts = [r["host"] for r in ranked[:4]]
        self.assertEqual(set(hosts),
                         {"h0.example.com", "h5.example.com",
                          "h10.example.com", "h15.example.com"})

    def test_max_candidates_limits_probes(self):
        with patch.object(fs, "fetch_serverlist", return_value=self.records), \
             patch.object(fs, "parse_servers", return_value=self.parsed), \
             patch.object(fs, "probe_latency_ms", return_value=1.0) as mp:
            fs.rank_servers(max_candidates=7)
        self.assertEqual(mp.call_count, 7)

    def test_failed_probes_excluded(self):
        calls = {"n": 0}

        def fake_probe(host, port, timeout=3.0):
            calls["n"] += 1
            return None if host == "h1.example.com" else 5.0

        with patch.object(fs, "fetch_serverlist", return_value=self.records), \
             patch.object(fs, "parse_servers", return_value=self.parsed), \
             patch.object(fs, "probe_latency_ms", side_effect=fake_probe):
            ranked = fs.rank_servers(max_candidates=3)
        self.assertEqual(calls["n"], 3)
        self.assertEqual(len(ranked), 2)

    def test_no_servers_after_filter(self):
        de_parsed = [{**self.SERVERS[0], "country_code": "DE"}]
        with patch.object(fs, "fetch_serverlist", return_value=self.records), \
             patch.object(fs, "parse_servers", return_value=de_parsed), \
             patch.object(fs, "probe_latency_ms") as mp:
            ranked = fs.rank_servers(country_filter="jp")
        mp.assert_not_called()
        self.assertEqual(ranked, [])

    def test_malformed_records_yield_no_candidates(self):
        with patch.object(fs, "fetch_serverlist", return_value=[{}, {}]), \
             patch.object(fs, "parse_servers", return_value=[]), \
             patch.object(fs, "probe_latency_ms") as mp:
            ranked = fs.rank_servers()
        mp.assert_not_called()
        self.assertEqual(ranked, [])


class TestConcurrencySafety(TestRankServers):

    def test_thread_safety_of_shared_pattern(self):
        def fake_probe(host, port, timeout=3.0):
            time.sleep(0.01)
            return float(hash(host) % 100)

        with patch.object(fs, "fetch_serverlist", return_value=self.records), \
             patch.object(fs, "parse_servers", return_value=self.parsed), \
             patch.object(fs, "probe_latency_ms", side_effect=fake_probe):
            ranked = fs.rank_servers(max_candidates=15)
        self.assertEqual(len(ranked), 15)
        self.assertEqual(
            [r["latency_ms"] for r in ranked],
            sorted(r["latency_ms"] for r in ranked))


if __name__ == "__main__":
    unittest.main()