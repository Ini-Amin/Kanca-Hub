"""Pure tests for scripts/farm_report.py (temp files only, no real DB)."""
from __future__ import annotations
import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import farm_report as F  # noqa: E402

class TestCountLiveThk(unittest.TestCase):
    def test_counts_only_live_prefix(self):
        accts = [{"api_key": "thk_live_aaa"}, {"api_key": "thk_test_bbb"},
                 {"key": "thk_live_ccc"}, {"api_key": ""}, {}]
        self.assertEqual(F.count_live_thk(accts), 2)

    def test_accepts_wrapped_dict(self):
        self.assertEqual(F.count_live_thk({"accounts": [{"api_key": "thk_live_x"}]}), 1)

    def test_non_list_garbage_is_zero(self):
        self.assertEqual(F.count_live_thk("nope"), 0)

class TestProviderSummary(unittest.TestCase):
    def _rows(self):
        return [
            ("groq", 1, json.dumps({"testStatus": "active"})),
            ("groq", 1, json.dumps({"testStatus": "unavailable"})),
            ("groq", 0, json.dumps({"testStatus": "error"})),
            ("exa", 1, json.dumps({"testStatus": "active"})),
            ("weird", 1, "not json"),          # malformed data -> not an error
            ("empty", 0, ""),                  # empty data -> not an error
        ]

    def test_totals_active_and_errors(self):
        s = F.provider_summary(self._rows())
        self.assertEqual(s["total"], 6)
        self.assertEqual(s["active"], 4)
        self.assertEqual(s["errors"], 2)          # unavailable + error
        self.assertEqual(s["by_provider"]["groq"], 3)

    def test_empty_input(self):
        self.assertEqual(F.provider_summary([]),
                         {"total": 0, "active": 0, "errors": 0, "by_provider": {}})

class TestFormatSms(unittest.TestCase):
    def test_seconds(self):
        self.assertEqual(F.format_sms("112233", 1_000_000, 1_012_000), "112233  (12s ago)")

    def test_minutes(self):
        self.assertEqual(F.format_sms("9", 1_000_000, 1_300_000), "9  (5m ago)")

    def test_hours(self):
        self.assertEqual(F.format_sms("9", 1_000_000, 8_200_000), "9  (2.0h ago)")

    def test_zero_ts_is_implausible(self):
        self.assertEqual(F.format_sms("9", 0, 300_000), "9  (age unknown)")

    def test_missing_code(self):
        self.assertEqual(F.format_sms(None, 123, 456), "none")

    def test_missing_ts(self):
        self.assertEqual(F.format_sms("77", None, 456), "77  (age unknown)")

    def test_negative_age_clamped(self):
        self.assertEqual(F.format_sms("1", 5_000_000, 1_000), "1  (0s ago)")

class TestFormatTable(unittest.TestCase):
    def test_full_table_is_stable(self):
        lines = F.format_table(
            thk_live=3,
            providers={"total": 2, "active": 1, "errors": 1, "by_provider": {"b": 1, "a": 1}},
            sms="112233  (1s ago)", webhook="https://x.trycloudflare.com")
        self.assertIn("  harbor thk live keys : 3", lines)
        self.assertTrue(any("2 total, 1 active, 1 testStatus error/unavailable" in l for l in lines))
        # providers sorted alphabetically
        idx = [l.strip().split()[0] for l in lines if l.startswith("      ")]
        self.assertEqual(idx, ["a", "b"])
        self.assertIn("  otp webhook          : https://x.trycloudflare.com", lines)

    def test_unavailable_providers(self):
        lines = F.format_table(thk_live=0, providers=None, sms="none", webhook="down")
        self.assertIn("  9router providers    : unavailable", lines)

class TestReadersUseTempFiles(unittest.TestCase):
    def test_read_harbor_from_temp(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "account.json"
            p.write_text(json.dumps([{"api_key": "thk_live_z"}, {"api_key": "other"}]))
            self.assertEqual(F.read_harbor_live(p), 1)

    def test_read_harbor_missing_is_zero(self):
        self.assertEqual(F.read_harbor_live(Path("/nonexistent/account.json")), 0)

    def test_read_providers_from_temp_db(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "data.sqlite"
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE providerConnections (id TEXT PRIMARY KEY, provider TEXT, "
                        "authType TEXT, name TEXT, email TEXT, priority INTEGER, isActive INTEGER, "
                        "data TEXT, createdAt TEXT, updatedAt TEXT)")
            con.executemany("INSERT INTO providerConnections VALUES (?,?,?,?,?,?,?,?,?,?)", [
                ("1", "groq", "key", None, None, 1, 1, json.dumps({"testStatus": "active"}), "", ""),
                ("2", "groq", "key", None, None, 1, 0, json.dumps({"testStatus": "unavailable"}), "", ""),
            ])
            con.commit()
            con.close()
            s = F.read_providers(db)
            self.assertEqual((s["total"], s["active"], s["errors"]), (2, 1, 1))

    def test_read_providers_missing_db_is_none(self):
        self.assertIsNone(F.read_providers(Path("/nonexistent/data.sqlite")))

    def test_read_sms_temp(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sms_latest.json"
            p.write_text(json.dumps({"code": "4242", "ts": 1_700_000_000_000}))
            self.assertEqual(F.read_sms(p, now_ms=1_700_000_005_000), "4242  (5s ago)")

    def test_read_sms_missing_is_none(self):
        self.assertEqual(F.read_sms(Path("/nonexistent/sms.json")), "none")

    def test_read_webhook_temp_and_missing(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "public_url.txt"
            p.write_text("https://abc.trycloudflare.com\n")
            self.assertEqual(F.read_webhook(p), "https://abc.trycloudflare.com")
        self.assertEqual(F.read_webhook(Path("/nonexistent/u.txt")), "down")

class TestNoKancahubDependency(unittest.TestCase):
    def test_import_does_not_pull_kancahub(self):
        code = ("import sys;sys.path.insert(0,%r);sys.path.insert(0,%r);"
                "import farm_report;"
                "raise SystemExit(1 if 'kancahub' in sys.modules else 0)"
                % (str(REPO), str(REPO / "scripts")))
        self.assertEqual(subprocess.call([sys.executable, "-c", code]), 0)

if __name__ == "__main__":
    unittest.main()