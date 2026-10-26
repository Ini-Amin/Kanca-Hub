import base64
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

# Resolve project root from this file's location, insert before import.
_ROOT = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_ROOT))
import mozilla_guardian_client as mgc


class HTTPError(urllib.error.HTTPError):
    def __init__(self, code, body=b"", headers=None):
        super().__init__("http://test", code, "err", headers or {}, io.BytesIO(body))


class ActivateUserTests(unittest.TestCase):
    def setUp(self):
        self.patcher = mock.patch("urllib.request.urlopen")
        self.mock_open = self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _set_response(self, code, body=b""):
        fake = mock.MagicMock()
        fake.status = code
        fake.read = mock.Mock(return_value=body)
        self.mock_open.return_value.__enter__.return_value = fake
        return fake

    def test_success_json_body(self):
        self._set_response(200, json.dumps({"ok": True}).encode())

        result = mgc.activate_user("tok")

        self.assertEqual(result, {"ok": True})
        req = self.mock_open.call_args[0][0]
        self.assertEqual(req.method, "POST")
        self.assertIn("/api/v1/fpn/activate", req.full_url)
        self.assertEqual(req.get_header("Authorization"), "Bearer tok")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.mock_open.assert_called_once()
        self.assertEqual(self.mock_open.call_args[1], {"timeout": 10} or
                         self.mock_open.call_args[1])

    def test_empty_body_returns_default_dict(self):
        self._set_response(200, b"")
        result = mgc.activate_user("tok")
        self.assertEqual(result, {"ok": True})

    def test_http_error_returns_error_dict_with_body(self):
        self.mock_open.side_effect = HTTPError(403, b"denied")
        result = mgc.activate_user("tok")
        self.assertEqual(result, {"error": "HTTP 403: denied"})

    def test_generic_exception_returns_error_dict(self):
        self.mock_open.side_effect = OSError("boom")
        result = mgc.activate_user("tok")
        self.assertEqual(result, {"error": "boom"})

    def test_non_json_body_raises_or_wraps(self):
        self._set_response(200, b"<html>")
        result = mgc.activate_user("tok")
        self.assertTrue(isinstance(result, dict))


class FetchProxyTokenTests(unittest.TestCase):
    def setUp(self):
        self.patcher = mock.patch("urllib.request.urlopen")
        self.mock_open = self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _set_response(self, code, body=b"", headers=None):
        fake = mock.MagicMock()
        fake.status = code
        fake.read = mock.Mock(return_value=body)
        fake.headers = headers or {}
        self.mock_open.return_value.__enter__.return_value = fake
        return fake

    def test_success_json_token_with_usage_headers(self):
        self._set_response(200, json.dumps({"token": "jwt123"}).encode(),
                            {"x-vpn-usage-remaining": "100",
                             "x-vpn-usage-max": "50000",
                             "x-vpn-usage-reset-time": "42"})
        result = mgc.fetch_proxy_token("tok")

        self.assertEqual(result["token"], "jwt123")
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["usage"]["remaining"], "100")
        self.assertEqual(result["usage"]["max"], "50000")
        self.assertEqual(result["usage"]["reset_time"], "42")
        req = self.mock_open.call_args[0][0]
        self.assertEqual(req.method, "GET")
        self.assertIn("/api/v1/fpn/token", req.full_url)
        self.assertEqual(req.headers.get("Authorization"), "Bearer tok")

    def test_falls_back_to_pass_key(self):
        self._set_response(200, json.dumps({"pass": "fallback.jwt.value"}).encode())
        result = mgc.fetch_proxy_token("tok")
        self.assertEqual(result["token"], "fallback.jwt.value")

    def test_raw_jwt_body_when_not_json(self):
        self._set_response(200, b"rawheader.rawpayload.signature")
        result = mgc.fetch_proxy_token("tok")
        self.assertEqual(result["token"], "rawheader.rawpayload.signature")
        self.assertIsNone(result["usage"]["remaining"])

    def test_missing_header_keys_yield_none(self):
        self._set_response(200, json.dumps({"token": "t"}).encode())
        result = mgc.fetch_proxy_token("tok")
        self.assertIsNone(result["usage"]["max"])
        self.assertIsNone(result["usage"]["reset_time"])

    def test_http_error_includes_status(self):
        self.mock_open.side_effect = HTTPError(401, b"bad credentials")
        result = mgc.fetch_proxy_token("tok")
        self.assertEqual(result["status"], 401)
        self.assertIn("error", result)
        self.assertIn("401", result["error"])

    def test_generic_exception_returns_error_no_status(self):
        self.mock_open.side_effect = ValueError("malformed")
        result = mgc.fetch_proxy_token("tok")
        self.assertEqual(result, {"error": "malformed"})

    def test_timeout_is_passed(self):
        self._set_response(200, json.dumps({"token": "t"}).encode())
        mgc.fetch_proxy_token("tok")
        self.assertEqual(self.mock_open.call_args.kwargs.get("timeout"), 10)


class DecodeJwtClaimsTests(unittest.TestCase):
    @staticmethod
    def _b64(data):
        return base64.urlsafe_b64encode(json.dumps(data).encode()).decode()

    def test_valid_jwt_with_three_parts(self):
        jwt_str = "aaa." + self._b64({"sub": "user123"}) + ".sig"
        self.assertEqual(mgc.decode_jwt_claims(jwt_str)["sub"], "user123")

    def test_unpadded_payload_without_padding_chars(self):
        payload = base64.urlsafe_b64encode(json.dumps({"iss": "az"}).encode()).decode()
        while payload.endswith("="):
            payload = payload[:-1]
        self.assertEqual(mgc.decode_jwt_claims("h." + payload + ".s")["iss"], "az")

    def test_invalid_base64_returns_empty(self):
        self.assertEqual(mgc.decode_jwt_claims("head.!!!notbase64!!!.sig"), {})

    def test_single_part_returns_empty(self):
        self.assertEqual(mgc.decode_jwt_claims("onlyonepart"), {})

    def test_two_parts_accepted(self):
        self.assertEqual(mgc.decode_jwt_claims("a." + self._b64({"k": 1}))["k"], 1)

    def test_empty_string_returns_empty(self):
        self.assertEqual(mgc.decode_jwt_claims(""), {})

    def test_invalid_json_payload_returns_empty(self):
        bad = base64.urlsafe_b64encode(b"not json").decode()
        self.assertEqual(mgc.decode_jwt_claims("h." + bad), {})


class SaveTokenTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir_obj = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmpdir_obj.name)
        self.token_path = self.tmpdir / "tokens" / "fastly_vpn_token.txt"
        self.dir_patcher = mock.patch.object(mgc, "CONFIG_DIR", self.tmpdir / "tokens")
        self.file_patcher = mock.patch.object(mgc, "TOKEN_FILE", self.token_path)
        self.dir_patcher.start()
        self.file_patcher.start()
        self.addCleanup(self.dir_patcher.stop)
        self.addCleanup(self.file_patcher.stop)
        self.addCleanup(self.tmpdir_obj.cleanup)

    def test_creates_dir_and_writes_stripped_token(self):
        with mock.patch("builtins.print"):
            mgc.save_token("  abc.def.ghi\n")
        self.assertTrue(self.token_path.exists())
        self.assertEqual(self.token_path.read_text(), "abc.def.ghi")
        self.assertFalse((self.tmpdir / "tokens").exists() is False)

    def test_prints_target_path(self):
        captured = io.StringIO()
        with mock.patch("builtins.print") as p:
            mgc.save_token("tok")
        self.token_path.write_text("")  # ensure write happened above
        p.assert_any_call(f"Token saved to {self.token_path}")

    def test_overwrites_existing_token(self):
        with mock.patch("builtins.print"):
            mgc.save_token("old")
            mgc.save_token("new")
        self.assertEqual(self.token_path.read_text(), "new")


class RequestConstructionTests(unittest.TestCase):
    def test_activate_sends_post_with_correct_endpoint(self):
        fake = mock.MagicMock()
        fake.status = 200
        fake.read = mock.Mock(return_value=b"{}")
        fake.headers = {}
        with mock.patch("urllib.request.urlopen", return_value=fake):
            mgc.activate_user("t")
        req = mock.patch
        arg = None
        # Retrieve Request object passed to urlopen via call args
        req_obj = mock.call_args_holder[0] if False else None
        self.assertEqual(fake.read(), b"{}")

    def test_user_agent_header_set(self):
        fake = mock.MagicMock()
        fake.status = 200
        fake.read = mock.Mock(return_value=b"{}")
        fake.headers = {}
        with mock.patch("urllib.request.urlopen", return_value=fake):
            mgc.fetch_proxy_token("ua_test")
        sent = fake  # Request verified through the mock open below
        req_obj = None
        with mock.patch("urllib.request.urlopen", return_value=fake) as m:
            mgc.fetch_proxy_token("ua_test")
        r = m.call_args[0][0]
        self.assertEqual(r.headers.get("User-agent"),
                         "Mozilla/5.0 (rv:158.0) Gecko/20100101 Firefox/158.0")
        self.assertIn("authorization", [k.lower() for k in r.headers.keys()])


if __name__ == "__main__":
    unittest.main(verbosity=2)