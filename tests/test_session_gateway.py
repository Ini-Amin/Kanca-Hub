"""Tests for the in-CLI SessionGateway (no real server started)."""
from __future__ import annotations
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, MagicMock

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import kancahub  # noqa: E402


class TestSessionGateway(unittest.TestCase):
    def test_alive_false_when_no_proc_and_no_server(self):
        gw = kancahub.SessionGateway(port=59999)
        self.assertFalse(gw.alive)

    def test_alive_true_when_proc_running(self):
        gw = kancahub.SessionGateway(port=59999)
        gw.proc = MagicMock()
        gw.proc.poll.return_value = None
        self.assertTrue(gw.alive)

    def test_alive_false_when_proc_exited(self):
        gw = kancahub.SessionGateway(port=59999)
        gw.proc = MagicMock()
        gw.proc.poll.return_value = 1
        self.assertFalse(gw.alive)

    def test_stop_terminates_proc(self):
        gw = kancahub.SessionGateway(port=59999)
        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        gw.proc = proc
        gw.stop()
        proc.terminate.assert_called_once()
        self.assertIsNone(gw.proc)

    def test_session_cmd_exists(self):
        p = kancahub.build_parser()
        args = p.parse_args(["session", "--port", "8899", "--start-gateway"])
        self.assertEqual(args.port, 8899)
        self.assertTrue(args.start_gateway)


if __name__ == "__main__":
    unittest.main()
