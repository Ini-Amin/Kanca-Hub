"""Tests for egress_node registry + pure helpers (no servers)."""
from __future__ import annotations
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import egress_node as en  # noqa: E402


class TestEgressNodeRegistry(unittest.TestCase):
    def test_add_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "nodes.json"
            en.add_node("phone", "100.77.106.64:8899", path=p)
            nodes = en.load_nodes(p)
            self.assertEqual(nodes["phone"], "http://100.77.106.64:8899")

    def test_remove_node(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "nodes.json"
            en.add_node("a", "http://1.2.3.4:8899", path=p)
            en.remove_node("a", path=p)
            self.assertEqual(en.load_nodes(p), {})

    def test_load_missing_is_empty(self):
        self.assertEqual(en.load_nodes("/nope/x.json"), {})

    def test_url_scheme_added(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "nodes.json"
            en.add_node("x", "1.2.3.4:8899", path=p)
            self.assertTrue(en.load_nodes(p)["x"].startswith("http://"))


class TestEgressLadderUsesNodes(unittest.TestCase):
    def test_auto_egress_prefers_a_healthy_node(self):
        import egress
        with patch.object(egress, "_load_egress_nodes", return_value={"ph": "http://10.0.0.9:8899"}), \
             patch.object(egress, "probe_status", return_value=200), \
             patch.object(egress, "check_gateway_egress", return_value="9.9.9.9"):
            gw, proc, src = egress.auto_egress("https://x/", mode="auto", verbose=False)
        self.assertEqual(gw, "http://10.0.0.9:8899")
        self.assertEqual(src, "node:ph")

    def test_auto_egress_skips_blocked_node(self):
        import egress
        with patch.object(egress, "_load_egress_nodes", return_value={"ph": "http://10.0.0.9:8899"}), \
             patch.object(egress, "probe_status", return_value=403), \
             patch.object(egress, "ensure_clean_egress", return_value=(None, None)), \
             patch.object(egress, "_check_warp_up", return_value=False), \
             patch.object(egress, "is_port_open", return_value=False), \
             patch.object(egress, "_phone_connected", return_value=False):
            gw, proc, src = egress.auto_egress("https://x/", mode="auto", verbose=False,
                                               prefer_pool="/nonexistent.txt")
        self.assertNotEqual(src, "node:ph")


if __name__ == "__main__":
    unittest.main()
