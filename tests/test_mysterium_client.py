#!/usr/bin/env python3
"""Comprehensive unit tests for mysterium_client.py."""

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
)

import mysterium_client as mc  # noqa: E402


def _mock_urlopen(payload: bytes):
    """Build a context-manager mock whose read() yields payload."""
    resp = mock.MagicMock()
    resp.read.return_value = payload
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return mock.patch("mysterium_client.urllib.request.urlopen", return_value=resp)


class TestApiRequest(unittest.TestCase):
    def test_get_request_default(self):
        payload = b'{"status": "ok"}'
        with _mock_urlopen(payload) as up:
            result = mc._api_request("/test")

        self.assertEqual(result, {"status": "ok"})
        req = up.call_args[0][0]
        self.assertEqual(
            req.full_url, "https://api.mysteriumvpn.com/api/v1/test"
        )
        self.assertEqual(req.get_method(), "GET")
        self.assertIsNone(req.data)
        self.assertNotIn("Authorization", req.headers)

    def test_post_with_body_and_token(self):
        payload = json.dumps({"created": True}).encode("utf-8")
        body = {"id": "abc-123"}
        token = "secret-token"
        with _mock_urlopen(payload) as up:
            result = mc._api_request(
                "/auth/activation", method="POST", body=body, token=token
            )

        self.assertEqual(result, {"created": True})
        req = up.call_args[0][0]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(
            req.full_url, "https://api.mysteriumvpn.com/api/v1/auth/activation"
        )
        self.assertEqual(
            json.loads(req.data.decode("utf-8")), {"id": "abc-123"}
        )
        self.assertEqual(
            req.headers.get("Authorization"), f"Bearer {token}"
        )
        self.assertEqual(
            req.headers.get("Content-type"), "application/json"
        )

    def test_full_url_passthrough(self):
        full = "https://example.com/api/v2/nodes"
        with _mock_urlopen(b'{}') as up:
            mc._api_request(full)
        req = up.call_args[0][0]
        self.assertEqual(req.full_url, full)

    def test_empty_response_returns_empty_dict(self):
        with _mock_urlopen(b"") as up:
            result = mc._api_request("/nothing")
        self.assertEqual(result, {})

    def test_default_headers_set(self):
        with _mock_urlopen(b'{"a": 1}') as up:
            mc._api_request("/t")
        req = up.call_args[0][0]
        self.assertEqual(
            req.headers.get("User-agent"), mc.USER_AGENT
        )
        self.assertEqual(req.headers.get("Accept"), "application/json")

    def test_timeout_forwarded(self):
        with _mock_urlopen(b'{}') as up:
            mc._api_request("/t", timeout=30)
        self.assertEqual(up.call_args[1]["timeout"], 30)

    def test_http_error_propagates(self):
        with mock.patch(
            "mysterium_client.urllib.request.urlopen",
            side_effect=urllib.error.HTTPError(
                "url", 401, "Unauthorized", None, io.BytesIO(b"")
            ),
        ):
            with self.assertRaises(urllib.error.HTTPError):
                mc._api_request("/denied")


class TestGetCurrentLocation(unittest.TestCase):
    def test_returns_parsed_json(self):
        data = {"ip": "1.2.3.4", "country": "US", "isp": "Comcast"}
        resp = mock.MagicMock()
        resp.read.return_value = json.dumps(data).encode("utf-8")
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False

        with mock.patch(
            "mysterium_client.urllib.request.urlopen", return_value=resp
        ) as up:
            result = mc.get_current_location()

        self.assertEqual(result, data)
        req = up.call_args[0][0]
        self.assertEqual(
            req.full_url, "https://location.mysterium.network/api/v1/location"
        )
        self.assertEqual(req.headers.get("User-agent"), mc.USER_AGENT)

    def test_invalid_json_raises(self):
        resp = mock.MagicMock()
        resp.read.return_value = b"not json at all"
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False

        with mock.patch(
            "mysterium_client.urllib.request.urlopen", return_value=resp
        ):
            with self.assertRaises(json.JSONDecodeError):
                mc.get_current_location()


class TestGetLocations(unittest.TestCase):
    def setUp(self):
        self.locations = [
            {"country": "US", "total": 100, "cities": ["NYC", "LA"]},
            {"country": "DE", "total": 50, "cities": ["BER"]},
            {"country": "jp", "total": 10, "cities": []},
        ]

    def test_no_filter_returns_all(self):
        with mock.patch.object(
            mc, "_api_request", return_value=self.locations
        ) as up:
            result = mc.get_locations()
        self.assertEqual(result, self.locations)
        up.assert_called_once_with("/connection/config/locations")

    def test_filter_matches_case_insensitive(self):
        with mock.patch.object(
            mc, "_api_request", return_value=self.locations
        ):
            result = mc.get_locations("us")
        self.assertEqual(
            result,
            [{"country": "US", "total": 100, "cities": ["NYC", "LA"]}],
        )

    def test_filter_no_match_returns_empty(self):
        with mock.patch.object(mc, "_api_request", return_value=self.locations):
            self.assertEqual(mc.get_locations("ZZ"), [])

    def test_non_list_response_returns_empty(self):
        for bad in ({"error": "oops"}, "string", 42, None):
            with mock.patch.object(mc, "_api_request", return_value=bad):
                self.assertEqual(mc.get_locations(), [])

    def test_missing_country_key_excluded_by_filter(self):
        data = [{"total": 5}, {"country": "US", "total": 1, "cities": []}]
        with mock.patch.object(mc, "_api_request", return_value=data):
            self.assertEqual(len(mc.get_locations("US")), 1)


class TestCreateActivationSession(unittest.TestCase):
    def test_session_id_is_valid_uuid_v4(self):
        with mock.patch.object(mc, "_api_request", return_value={"ok": True}):
            result = mc.create_activation_session()

        sid = result["session_id"]
        parsed = uuid.UUID(sid)
        self.assertEqual(parsed.version, 4)
        self.assertEqual(result["result"], {"ok": True})

    def test_two_sessions_have_different_ids(self):
        with mock.patch.object(mc, "_api_request", return_value={}):
            s1 = mc.create_activation_session()
            s2 = mc.create_activation_session()
        self.assertNotEqual(s1["session_id"], s2["session_id"])

    def test_request_payload_and_endpoint(self):
        with mock.patch.object(mc, "_api_request", return_value={}) as up:
            mc.create_activation_session()
        self.assertEqual(up.call_args[1]["method"], "POST")
        self.assertEqual(up.call_args[0][0], "/auth/activation")
        self.assertEqual(list(up.call_args[1]["body"].keys()), ["id"])


class TestCheckActivationStatus(unittest.TestCase):
    def test_calls_correct_endpoint(self):
        session_id = "1234-abcd"
        expected = {"status": "pending"}
        with mock.patch.object(mc, "_api_request", return_value=expected) as up:
            result = mc.check_activation_status(session_id)
        self.assertEqual(result, expected)
        self.assertEqual(
            up.call_args[0][0], f"/auth/activation/{session_id}"
        )
        self.assertEqual(up.call_args[1]["method"], "GET")


class TestConnectProxy(unittest.TestCase):
    def test_payload_and_token(self):
        payload = {"proxy": "socks5://1.2.3.4:1080"}
        with mock.patch.object(mc, "_api_request", return_value=payload) as up:
            result = mc.connect_proxy("mytoken", country="us", ip_type="cellular")

        self.assertEqual(result, payload)
        self.assertEqual(up.call_args[0][0], "/connection/connect-proxy")
        self.assertEqual(up.call_args[1]["method"], "POST")
        self.assertEqual(up.call_args[1]["token"], "mytoken")
        self.assertEqual(
            up.call_args[1]["body"],
            {
                "country": "US",
                "ip_type": "cellular",
                "os_type": "web",
                "reset_connection": True,
            },
        )

    def test_default_arguments(self):
        with mock.patch.object(mc, "_api_request", return_value={}) as up:
            mc.connect_proxy("tok")
        body = up.call_args[1]["body"]
        self.assertEqual(body["country"], "US")
        self.assertEqual(body["ip_type"], "residential")


class TestMain(unittest.TestCase):
    def _run_main(self, argv):
        buf = io.StringIO()
        with mock.patch("sys.argv", ["mysterium_client.py"] + argv):
            with redirect_stdout(buf):
                mc.main()
        return buf.getvalue()

    def test_ip_command(self):
        loc = {
            "ip": "8.8.8.8",
            "country": "US",
            "city": "Ashburn",
            "region": "VA",
            "isp": "GCP",
            "asn": 15169,
            "node_type": "residential",
        }
        with mock.patch.object(mc, "get_current_location", return_value=loc):
            out = self._run_main(["ip"])
        self.assertIn("8.8.8.8", out)
        self.assertIn("US", out)
        self.assertIn("GCP", out)
        self.assertIn("15169", out)

    def test_locations_command(self):
        locs = [
            {"country": "US", "total": 100, "cities": ["NYC", "LA"]},
            {"country": "DE", "total": 50, "cities": ["BER"]},
        ]
        with mock.patch.object(mc, "get_locations", return_value=locs) as gl:
            out = self._run_main(["locations", "--country", "US"])
        self.assertEqual(gl.call_args[1]["country_filter"], "US")
        self.assertIn("US", out)
        self.assertIn("100", out)

    def test_locations_command_empty(self):
        with mock.patch.object(mc, "get_locations", return_value=[]):
            self._run_main(["locations"])

    def test_activate_command(self):
        act = {"session_id": "sid-42", "result": {}}
        with mock.patch.object(
            mc, "create_activation_session", return_value=act
        ), mock.patch.object(
            mc, "check_activation_status", return_value={"status": "pending"}
        ) as chk:
            out = self._run_main(["activate"])
        self.assertIn("sid-42", out)
        self.assertEqual(chk.call_args[0][0], "sid-42")

    def test_connect_command_with_token_arg(self):
        with mock.patch.object(
            mc,
            "connect_proxy",
            return_value={"proxy": "socks5://0.0.0.0:1"},
        ) as cp:
            out = self._run_main(
                ["connect", "--country", "DE", "--type", "hosting", "--token", "tk123"]
            )
        self.assertEqual(cp.call_args[1]["country"], "DE")
        self.assertEqual(cp.call_args[1]["ip_type"], "hosting")
        self.assertIn("socks5://0.0.0.0:1", out)

    def test_connect_command_no_token_exits(self):
        tmpdir = tempfile.mkdtemp()
        with mock.patch.object(
            mc, "TOKEN_FILE", Path(tmpdir) / "tok.json"
        ):
            with self.assertRaises(SystemExit) as ctx:
                self._run_main(["connect"])
            self.assertEqual(ctx.exception.code, 1)

    def test_connect_command_token_from_file(self):
        tmpdir = tempfile.mkdtemp()
        token_path = Path(tmpdir) / "tok.json"
        token_path.write_text(json.dumps({"token": "file-token"}))

        with mock.patch.object(mc, "TOKEN_FILE", token_path), mock.patch.object(
            mc, "connect_proxy", return_value={"ok": True}
        ) as cp:
            self._run_main(["connect"])
        self.assertEqual(cp.call_args[0][0], "file-token")

    def test_connect_command_http_error_handled(self):
        err = urllib.error.HTTPError(
            "url", 401, "Unauthorized", None, io.BytesIO(b"bad token")
        )
        with mock.patch.object(
            mc, "TOKEN_FILE", Path(tempfile.mkdtemp()) / "tok.json"
        ), mock.patch.object(
            mc, "connect_proxy", side_effect=err
        ):
            buf = io.StringIO()
            with mock.patch("sys.argv", ["mysterium_client.py", "connect", "--token", "x"]):
                with redirect_stdout(buf):
                    mc.main()
            self.assertIn("HTTP Error 401", buf.getvalue())
        err.fp.close()

    def test_no_subcommand_prints_help(self):
        out = self._run_main([])
        self.assertIn("usage", out.lower())


if __name__ == "__main__":
    unittest.main()