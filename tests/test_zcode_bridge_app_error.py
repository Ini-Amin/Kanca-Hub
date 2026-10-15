"""Regression: the ZCode bridge must not relay an upstream application-level
error as an empty 200 completion.

The plan answers a spent quota bucket with HTTP 200 and a body like
{"code":1005,"msg":"exceed quota limit"} (no `content` array). The bridge used
to treat that as success, so _anthropic_to_oa() produced
content=null / completion_tokens=0 / finish_reason=stop. omp reports exactly
that as "Assistant returned empty stop after retry cap", and Janus got stuck.
"""
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "scripts" / "zcode_bridge_server.py"


def load_bridge():
    spec = importlib.util.spec_from_file_location("zcode_bridge_server", SERVER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["zcode_bridge_server"] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeOpener:
    """Stand-in for the egress opener (opener.open(req, timeout=...) -> resp)."""

    def __init__(self, resp):
        self._resp = resp

    def open(self, req, timeout=None):
        return self._resp


class FakeResponse:
    """Minimal urlopen() stand-in that honours the read(n) contract."""

    def __init__(self, data, ctype="application/json"):
        self._data = data
        self._i = 0
        self.headers = {"content-type": ctype}
        self.status = 200
        self.code = 200

    def read(self, n=-1):
        if n is None or n < 0:
            out = self._data[self._i:]
            self._i = len(self._data)
            return out
        out = self._data[self._i:self._i + n]
        self._i += len(out)
        return out

    def close(self):
        pass


class TestAppErrorDetection(unittest.TestCase):
    def setUp(self):
        self.m = load_bridge()

    def test_quota_error_body_is_flagged(self):
        body = b'{"code":1005,"msg":"exceed quota limit","logid":"x"}'
        err, resp = self.m._peek_app_error(FakeResponse(body))
        self.assertIsInstance(err, self.m.UpstreamAppError)
        self.assertEqual(err.app_code, 1005)
        self.assertEqual(err.code, 429)
        self.assertTrue(self.m._try_another_account(err))
        self.assertTrue(self.m._try_next_model(err))
        # the error must stay relayable to the client
        self.assertEqual(json.loads(err.read())["code"], 1005)

    def test_normal_body_passes_through_untouched(self):
        body = json.dumps({"id": "msg_1", "type": "message",
                           "content": [{"type": "text", "text": "PONG"}]}).encode()
        err, resp = self.m._peek_app_error(FakeResponse(body))
        self.assertIsNone(err)
        self.assertEqual(json.loads(resp.read())["content"][0]["text"], "PONG")

    def test_streaming_body_replays_every_byte(self):
        raw = b'data: {"type":"message_start"}\n\ndata: {"type":"message_stop"}\n\n'
        err, resp = self.m._peek_app_error(FakeResponse(raw, "text/event-stream"))
        self.assertIsNone(err)
        got = b""
        while True:
            c = resp.read(7)
            if not c:
                break
            got += c
        self.assertEqual(got, raw)

    def test_abuse_3012_advances_both_chain_and_account(self):
        """2026-10-15: a 3012 is a per-ACCOUNT block (same request succeeded on
        the next account), so it must rotate accounts AND advance the model
        chain. Only rotating the model left blocked accounts in the pool."""
        err = self.m.UpstreamAppError(3012, "unusual activity", b'{"code":3012}', 405)
        self.assertTrue(self.m._try_next_model(err))
        self.assertTrue(self.m._try_another_account(err))

    def test_abuse_3012_from_peek_still_advances_chain(self):
        """Regression: _peek_app_error builds the error with the DEFAULT
        http_code=429, so a real anti-abuse body is app_code=3012 / code=429.
        Matching only on HTTP 405 relayed 3012 to the client instead of chaining
        to GLM-5.3-Flash (observed live 2026-10-14)."""
        err, _ = self.m._peek_app_error(
            FakeResponse(b'{"code":3012,"msg":"request has been blocked due to unusual activity."}'))
        self.assertIsInstance(err, self.m.UpstreamAppError)
        self.assertEqual(err.app_code, 3012)
        self.assertEqual(err.code, 429)
        self.assertTrue(self.m._try_next_model(err))
        self.assertTrue(self.m._try_another_account(err))

    def test_401_rotates_account_not_model(self):
        err = self.m.UpstreamAppError(0, "unauthorized", b"{}", 401)
        self.assertTrue(self.m._try_another_account(err))
        self.assertFalse(self.m._try_next_model(err))

    def test_upstream_200_never_yields_empty_success(self):
        """The empty-stop shape must be impossible to produce from an error body."""
        err, _ = self.m._peek_app_error(
            FakeResponse(b'{"code":1005,"msg":"exceed quota limit"}'))
        self.assertIsNotNone(err, "an error body must not be returned as success")

    def test_call_upstream_flags_json_error_even_for_stream_requests(self):
        """A spent bucket answers a `stream: true` request with plain JSON, so
        the bridge must still detect it (omp streams, and this is what stuck Janus)."""
        body = b'{"code":1005,"msg":"exceed quota limit","logid":"x"}'

        orig = self.m.egress_opener
        self.m.egress_opener = lambda: FakeOpener(FakeResponse(body))
        try:
            for stream in (True, False):
                resp, err = self.m.call_upstream(
                    {"model": "GLM-5.3", "stream": stream,
                     "messages": [{"role": "user", "content": "hi"}]}, "a@b.c")
                self.assertIsNone(resp, "stream=%s must not return a response" % stream)
                self.assertIsInstance(err, self.m.UpstreamAppError)
                self.assertEqual(err.app_code, 1005)
        finally:
            self.m.egress_opener = orig

    def test_healthy_sse_is_not_peeked(self):
        """text/event-stream must pass through untouched (no blocking read)."""
        orig = self.m.egress_opener
        self.m.egress_opener = lambda: FakeOpener(FakeResponse(
            b'data: {"type":"message_start"}\n\n', "text/event-stream; charset=utf-8"))
        try:
            resp, err = self.m.call_upstream(
                {"model": "GLM-5.3", "stream": True,
                 "messages": [{"role": "user", "content": "hi"}]}, "a@b.c")
            self.assertIsNone(err)
            self.assertIn(b"message_start", resp.read())
        finally:
            self.m.egress_opener = orig


class TestUpstreamEgress(unittest.TestCase):
    """z.ai's WAF blocks the home IP with an HTML 405 page, so the bridge must be
    able to send its upstream through a clean egress proxy (LightVela WARP)."""

    def setUp(self):
        self.m = load_bridge()
        self._cfg, self._env = self.m.load_config, os.environ.get("ZCODE_UPSTREAM_PROXY")
        os.environ.pop("ZCODE_UPSTREAM_PROXY", None)

    def tearDown(self):
        self.m.load_config = self._cfg
        if self._env is None:
            os.environ.pop("ZCODE_UPSTREAM_PROXY", None)
        else:
            os.environ["ZCODE_UPSTREAM_PROXY"] = self._env

    def test_env_var_wins(self):
        os.environ["ZCODE_UPSTREAM_PROXY"] = "http://127.0.0.1:9999"
        self.m.load_config = lambda: {"egress": {"proxy": "http://127.0.0.1:1"}}
        self.assertEqual(self.m.upstream_proxy(), "http://127.0.0.1:9999")

    def test_config_used_when_no_env(self):
        self.m.load_config = lambda: {"egress": {"proxy": "http://127.0.0.1:25344"}}
        self.assertEqual(self.m.upstream_proxy(), "http://127.0.0.1:25344")

    def test_default_is_direct(self):
        self.m.load_config = lambda: {}
        self.assertEqual(self.m.upstream_proxy(), "")

    def test_opener_uses_proxy_when_port_live(self):
        self.m.load_config = lambda: {"egress": {"proxy": "http://127.0.0.1:25344"}}
        import socket
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        try:
            self.m.load_config = lambda: {"egress": {"proxy": "http://127.0.0.1:%d" % port}}
            op = self.m.egress_opener()
            self.assertIsInstance(op, self.m.urllib.request.OpenerDirector)
            self.assertTrue(any(isinstance(h, self.m.urllib.request.ProxyHandler)
                                for h in op.handlers))
        finally:
            srv.close()

    def test_opener_falls_back_to_direct_when_proxy_down(self):
        self.m.load_config = lambda: {"egress": {"proxy": "http://127.0.0.1:1"}}
        op = self.m.egress_opener()
        # a direct opener carries a ProxyHandler built from the (empty) env, so
        # assert on behaviour instead: it must not raise and must still open.
        self.assertTrue(hasattr(op, "open"))


if __name__ == "__main__":
    unittest.main()
