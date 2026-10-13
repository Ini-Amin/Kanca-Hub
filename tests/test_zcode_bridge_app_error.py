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

    def test_abuse_405_advances_model_chain_not_account(self):
        err = self.m.UpstreamAppError(3012, "unusual activity", b'{"code":3012}', 405)
        self.assertTrue(self.m._try_next_model(err))
        self.assertFalse(self.m._try_another_account(err))

    def test_401_rotates_account_not_model(self):
        err = self.m.UpstreamAppError(0, "unauthorized", b"{}", 401)
        self.assertTrue(self.m._try_another_account(err))
        self.assertFalse(self.m._try_next_model(err))

    def test_upstream_200_never_yields_empty_success(self):
        """The empty-stop shape must be impossible to produce from an error body."""
        err, _ = self.m._peek_app_error(
            FakeResponse(b'{"code":1005,"msg":"exceed quota limit"}'))
        self.assertIsNotNone(err, "an error body must not be returned as success")


if __name__ == "__main__":
    unittest.main()
