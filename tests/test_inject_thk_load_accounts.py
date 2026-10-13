"""Regression tests for the key loaders in scripts/inject_thk_9router.py.

The script now serves two key sources behind one upsert path:
  load_harbor(path) -> [{"key","name","email"}]   (TokenHarbor account.json)
  load_zcode(path)  -> [{"key","name","email"}]   (bridge keys.txt `key=email`)
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.inject_thk_9router import load_harbor, load_zcode, SOURCES, ensure_round_robin


def _write_tmp_json(data) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        return f.name


class TestLoadHarbor(unittest.TestCase):
    def test_bare_single_account(self):
        # (a) bare single-account dict with api_key parsed to 1-element list
        sample = {
            "email": "th_test@kancalabs.my.id",
            "api_key": "thk_samplekey123",
            "created_at": "2026-10-05T00:00:00Z",
        }
        path = _write_tmp_json(sample)
        try:
            res = load_harbor(path)
            self.assertEqual(len(res), 1)
            self.assertEqual(res[0]["key"], "thk_samplekey123")
            self.assertEqual(res[0]["email"], "th_test@kancalabs.my.id")
            self.assertEqual(res[0]["name"], "th_test@kancalabs.my.id")
        finally:
            Path(path).unlink(missing_ok=True)

    def test_wrapped_accounts_dict(self):
        # (b) wrapped {'accounts': [...]} list works
        sample = {
            "accounts": [
                {"email": "a1@test.com", "api_key": "thk_key1"},
                {"email": "a2@test.com", "api_key": "thk_key2"},
            ]
        }
        path = _write_tmp_json(sample)
        try:
            res = load_harbor(path)
            self.assertEqual([r["key"] for r in res], ["thk_key1", "thk_key2"])
        finally:
            Path(path).unlink(missing_ok=True)

    def test_json_list(self):
        # (c) JSON list works
        sample = [
            {"email": "a1@test.com", "api_key": "thk_key1"},
            {"email": "a2@test.com", "api_key": "thk_key2"},
        ]
        path = _write_tmp_json(sample)
        try:
            res = load_harbor(path)
            self.assertEqual([r["key"] for r in res], ["thk_key1", "thk_key2"])
        finally:
            Path(path).unlink(missing_ok=True)

    def test_empty_or_irrelevant_dict(self):
        # (d) empty/irrelevant dict returns []
        for sample in ({}, {"irrelevant": "field"}, {"status": "ok", "value": 42}):
            path = _write_tmp_json(sample)
            try:
                self.assertEqual(load_harbor(path), [])
            finally:
                Path(path).unlink(missing_ok=True)

    def test_non_thk_keys_are_dropped(self):
        sample = [{"email": "a@test.com", "api_key": "sk-other"},
                  {"email": "b@test.com", "api_key": "thk_keep"}]
        path = _write_tmp_json(sample)
        try:
            res = load_harbor(path)
            self.assertEqual([r["key"] for r in res], ["thk_keep"])
        finally:
            Path(path).unlink(missing_ok=True)


class TestLoadZcode(unittest.TestCase):
    def _write(self, text: str) -> str:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write(text)
            return f.name

    def test_parses_key_equals_email(self):
        p = self._write("sk-zc-01-aaa=one@x.id\nsk-zc-02-bbb=two@x.id\n")
        try:
            res = load_zcode(p)
            self.assertEqual([r["key"] for r in res], ["sk-zc-01-aaa", "sk-zc-02-bbb"])
            self.assertEqual([r["email"] for r in res], ["one@x.id", "two@x.id"])
        finally:
            Path(p).unlink(missing_ok=True)

    def test_skips_blanks_and_comments(self):
        p = self._write("\n# a comment\nsk-zc-01-aaa=one@x.id\n   \n")
        try:
            res = load_zcode(p)
            self.assertEqual(len(res), 1)
            self.assertEqual(res[0]["key"], "sk-zc-01-aaa")
        finally:
            Path(p).unlink(missing_ok=True)

    def test_missing_file_is_empty(self):
        self.assertEqual(load_zcode("/nonexistent/keys.txt"), [])


class TestSourceTable(unittest.TestCase):
    def test_zcode_source_is_one_per_email_and_not_verified(self):
        z = SOURCES["zcode"]
        self.assertTrue(z["one_per_email"])
        self.assertFalse(z["verify"])          # the bridge has no cheap probe
        self.assertEqual(z["prefix"], "Zcode")
        self.assertEqual(z["base"], "http://127.0.0.1:8787/v1")

    def test_zcode_source_wants_round_robin(self):
        # one connection row per account -> rotate, else 3009s under concurrency
        self.assertTrue(SOURCES["zcode"]["round_robin"])
        self.assertNotIn("round_robin", SOURCES["thk"])   # THK stays fallback

    def test_thk_source_is_per_key_and_verified(self):
        t = SOURCES["thk"]
        self.assertFalse(t["one_per_email"])
        self.assertTrue(t["verify"])
        self.assertEqual(t["prefix"], "THK")


class TestEnsureRoundRobin(unittest.TestCase):
    """settings.data.providerStrategies[node] is what the 9Router dashboard
    PATCHes. One row per account means 'fallback' pins the first row."""

    def _con(self):
        import sqlite3
        con = sqlite3.connect(":memory:")
        con.execute("CREATE TABLE settings (id INTEGER PRIMARY KEY CHECK (id = 1),"
                    " data TEXT NOT NULL)")
        con.execute("INSERT INTO settings (id, data) VALUES (1, '{}')")
        return con

    def test_turns_round_robin_on(self):
        con = self._con()
        src = dict(SOURCES["zcode"])
        self.assertTrue(ensure_round_robin(con, src, False))
        data = json.loads(con.execute("SELECT data FROM settings WHERE id=1").fetchone()[0])
        got = data["providerStrategies"][src["node_id"]]
        self.assertEqual(got["fallbackStrategy"], "round-robin")
        self.assertEqual(got["stickyRoundRobinLimit"], 1)
        con.close()

    def test_idempotent(self):
        con = self._con()
        src = dict(SOURCES["zcode"])
        ensure_round_robin(con, src, False)
        self.assertFalse(ensure_round_robin(con, src, False))   # already on
        con.close()

    def test_dry_run_writes_nothing(self):
        con = self._con()
        src = dict(SOURCES["zcode"])
        self.assertTrue(ensure_round_robin(con, src, True))
        data = json.loads(con.execute("SELECT data FROM settings WHERE id=1").fetchone()[0])
        self.assertEqual(data, {})                              # untouched
        con.close()

    def test_preserves_other_strategies(self):
        con = self._con()
        con.execute("UPDATE settings SET data=? WHERE id=1",
                    (json.dumps({"providerStrategies": {"codex":
                     {"fallbackStrategy": "round-robin", "stickyRoundRobinLimit": 1}}}),))
        ensure_round_robin(con, dict(SOURCES["zcode"]), False)
        data = json.loads(con.execute("SELECT data FROM settings WHERE id=1").fetchone()[0])
        self.assertIn("codex", data["providerStrategies"])
        con.close()

    def test_non_round_robin_source_is_a_noop(self):
        con = self._con()
        self.assertFalse(ensure_round_robin(con, dict(SOURCES["thk"]), False))
        data = json.loads(con.execute("SELECT data FROM settings WHERE id=1").fetchone()[0])
        self.assertEqual(data, {})
        con.close()


if __name__ == "__main__":
    unittest.main()
