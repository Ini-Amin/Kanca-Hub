"""Pure tests for scripts/egress_probe.py (no network)."""
from __future__ import annotations
import sys
from pathlib import Path
import unittest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import egress_probe as E  # noqa: E402

class TestParseInfo(unittest.TestCase):
    def test_parses_json_bytes(self):
        info = E.parse_info(b'{"query":"1.2.3.4","isp":"Telkom","hosting":false}')
        self.assertEqual(info["query"], "1.2.3.4")
        self.assertEqual(info["isp"], "Telkom")

    def test_parses_json_str(self):
        self.assertEqual(E.parse_info('{"query":"5.6.7.8"}')["query"], "5.6.7.8")

    def test_rejects_non_object(self):
        with self.assertRaises(ValueError):
            E.parse_info("[1,2]")

    def test_rejects_bad_json(self):
        with self.assertRaises(ValueError):
            E.parse_info("<html>nope</html>")

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            E.parse_info(b"")

class TestFormatVerdict(unittest.TestCase):
    def test_proxy_flagged(self):
        line = E.format_verdict({"query": "9.9.9.9", "isp": "X", "proxy": True})
        self.assertTrue(line.startswith("9.9.9.9  FLAGGED  "))

    def test_mobile_strong(self):
        self.assertIn("STRONG", E.format_verdict({"query": "1.1.1.1", "mobile": True}))

    def test_hosting_weak_beats_mobile(self):
        # classify_egress checks hosting before mobile
        self.assertIn("WEAK", E.format_verdict({"query": "1.1.1.1", "hosting": True, "mobile": True}))

    def test_residential_ok_includes_isp(self):
        line = E.format_verdict({"query": "8.8.8.8", "isp": "Comcast"})
        self.assertIn("OK", line)
        self.assertTrue(line.endswith("[Comcast]"))

    def test_missing_fields_safe(self):
        line = E.format_verdict({})
        self.assertIn("?", line)
        self.assertIn("OK", line)
        self.assertTrue(line.endswith("[?]"))

class TestNoKancahubDependency(unittest.TestCase):
    def test_import_does_not_pull_kancahub(self):
        # Fresh interpreter: importing egress_probe must not load kancahub.
        import subprocess
        code = ("import sys;sys.path.insert(0,%r);sys.path.insert(0,%r);"
                "import egress_probe;"
                "raise SystemExit(1 if 'kancahub' in sys.modules else 0)"
                % (str(REPO), str(REPO / "scripts")))
        self.assertEqual(subprocess.call([sys.executable, "-c", code]), 0)

if __name__ == "__main__":
    unittest.main()