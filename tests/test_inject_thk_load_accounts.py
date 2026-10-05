"""Regression tests for load_accounts in scripts/inject_thk_9router.py."""

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

from scripts.inject_thk_9router import load_accounts


def _write_tmp_json(data) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(data, f)
        return f.name


class TestInjectThkLoadAccounts(unittest.TestCase):
    def test_bare_single_account(self):
        # (a) bare single-account dict with api_key parsed to 1-element list
        sample = {
            "email": "th_test@kancalabs.my.id",
            "api_key": "thk_samplekey123",
            "created_at": "2026-10-05T00:00:00Z",
        }
        path = _write_tmp_json(sample)
        try:
            res = load_accounts(path)
            self.assertEqual(len(res), 1)
            self.assertEqual(res[0]["api_key"], "thk_samplekey123")
            self.assertEqual(res[0]["email"], "th_test@kancalabs.my.id")
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
            res = load_accounts(path)
            self.assertEqual(len(res), 2)
            self.assertEqual(res[0]["api_key"], "thk_key1")
            self.assertEqual(res[1]["api_key"], "thk_key2")
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
            res = load_accounts(path)
            self.assertEqual(len(res), 2)
            self.assertEqual(res[0]["api_key"], "thk_key1")
            self.assertEqual(res[1]["api_key"], "thk_key2")
        finally:
            Path(path).unlink(missing_ok=True)

    def test_empty_or_irrelevant_dict(self):
        # (d) empty/irrelevant dict returns []
        for sample in ({}, {"irrelevant": "field"}, {"status": "ok", "value": 42}):
            path = _write_tmp_json(sample)
            try:
                res = load_accounts(path)
                self.assertEqual(res, [])
            finally:
                Path(path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
