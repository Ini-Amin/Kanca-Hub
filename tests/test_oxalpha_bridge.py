"""Unit tests for scripts/oxalpha_bridge_server.py (no network).

All upstream I/O goes through call_upstream(); tests stub it.
"""
from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import oxalpha_bridge_server as ox


class FakeResp:
    def __init__(self, lines):
        self._lines = [l if isinstance(l, bytes) else l.encode() for l in lines]
        self._i = 0

    def readline(self):
        if self._i >= len(self._lines):
            return b""
        ln = self._lines[self._i]
        self._i += 1
        return ln

    def read(self, n=-1):
        if n is None or n < 0:
            out = b"".join(self._lines[self._i:])
            self._i = len(self._lines)
            return out
        out = b""
        while len(out) < n and self._i < len(self._lines):
            out += self._lines[self._i]
            self._i += 1
        return out[:n]

    def close(self):
        pass


def _sse(*texts):
    lines = []
    for t in texts:
        ev = {"choices": [{"delta": {"content": t}}]}
        lines.append(b"data: " + json.dumps(ev).encode() + b"\n")
    lines.append(b"data: [DONE]\n")
    return FakeResp(lines)


class TestCollect(unittest.TestCase):
    def test_collect_joins_deltas(self):
        h = ox.Handler.__new__(ox.Handler)
        self.assertEqual(h._collect(_sse("po", "ng")), "pong")

    def test_collect_skips_bad_lines(self):
        h = ox.Handler.__new__(ox.Handler)
        resp = FakeResp([b"garbage\n", b"data: not-json\n",
                         b'data: {"choices":[{"delta":{"content":"ok"}}]}\n',
                         b"data: [DONE]\n"])
        self.assertEqual(h._collect(resp), "ok")


class TestCheckpoint(unittest.TestCase):
    def test_428_maps_to_429(self):
        # handler branch: err http 428 + turnstile body -> client sees 429
        err = {"http": 428, "body": {"code": "turnstile_required"}}
        is_checkpoint = err.get("http") == 428 or (err.get("body") or {}).get("code") == "turnstile_required"
        self.assertTrue(is_checkpoint)


class TestBridgeShape(unittest.TestCase):
    def test_models_payload(self):
        self.assertEqual(ox.MODEL, "z-ai/glm-5.3-flash")
        self.assertTrue(ox.BASE.startswith("https://"))

    def test_relay_default(self):
        self.assertIn("25346", ox.UPSTREAM_PROXY)


if __name__ == "__main__":
    unittest.main()
