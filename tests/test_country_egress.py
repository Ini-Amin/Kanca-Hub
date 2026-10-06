"""
tests/test_country_egress.py — Unit tests for country-filtered egress routing.

Validates:
1. Truth table for pure helper `country_allowed(cc, allow, exclude, resolved: bool) -> bool`.
   - When resolved=False: keep unless allow is specified.
   - When resolved=True: enforce exclude set and allow set.
2. TokenHarbor (`thk`) egress configuration in `scripts/kancahub.py` excludes 'ID'.
3. `validate()` in `scripts/proxy_lib.py` accepts country filtering parameters.
4. `auto_egress()` in `scripts/egress.py` accepts country filtering parameters.
5. `kancahub thk batch` CLI subparser exposes `--country`.
"""

from __future__ import annotations

import inspect
import unittest
from pathlib import Path

# Ensure repo root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
import sys
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from scripts.proxy_lib import country_allowed, validate
from scripts.egress import auto_egress
from scripts.kancahub import EGRESS_COUNTRY, build_parser


class TestCountryAllowedTruthTable(unittest.TestCase):
    """Truth table tests for pure helper country_allowed."""

    def test_unresolved_truth_table(self) -> None:
        """When lookup failed/unresolved: keep (return True) UNLESS allow is set."""
        # Unresolved with exclude only: degrade gracefully and keep
        self.assertTrue(country_allowed("ID", allow=None, exclude={"ID"}, resolved=False))
        self.assertTrue(country_allowed(None, allow=None, exclude={"ID"}, resolved=False))
        self.assertTrue(country_allowed("", allow=None, exclude={"ID"}, resolved=False))
        self.assertTrue(country_allowed(None, allow=None, exclude=None, resolved=False))
        self.assertTrue(country_allowed(None, allow=set(), exclude={"ID"}, resolved=False))

        # Unresolved with allow set: cannot confirm country, so must drop
        self.assertFalse(country_allowed("US", allow={"US"}, exclude=None, resolved=False))
        self.assertFalse(country_allowed(None, allow={"US"}, exclude=None, resolved=False))
        self.assertFalse(country_allowed("", allow={"US"}, exclude=None, resolved=False))
        self.assertFalse(country_allowed(None, allow={"US", "SG"}, exclude={"ID"}, resolved=False))

    def test_resolved_exclude_truth_table(self) -> None:
        """When resolved: reject if country code is in exclude."""
        # Exact match in exclude
        self.assertFalse(country_allowed("ID", allow=None, exclude={"ID"}, resolved=True))
        # Case insensitive match
        self.assertFalse(country_allowed("id", allow=None, exclude={"ID"}, resolved=True))
        self.assertFalse(country_allowed("ID", allow=None, exclude={"id"}, resolved=True))
        self.assertFalse(country_allowed("  id  ", allow=None, exclude={"ID"}, resolved=True))

        # Non-excluded countries pass
        self.assertTrue(country_allowed("US", allow=None, exclude={"ID"}, resolved=True))
        self.assertTrue(country_allowed("SG", allow=None, exclude={"ID"}, resolved=True))
        self.assertTrue(country_allowed("DE", allow=None, exclude={"ID"}, resolved=True))
        self.assertTrue(country_allowed("JP", allow=None, exclude={"ID"}, resolved=True))

        # Empty or None cc is not in exclude set
        self.assertTrue(country_allowed("", allow=None, exclude={"ID"}, resolved=True))
        self.assertTrue(country_allowed(None, allow=None, exclude={"ID"}, resolved=True))

    def test_resolved_allow_truth_table(self) -> None:
        """When resolved with allow list: accept only if cc in allow."""
        self.assertTrue(country_allowed("US", allow={"US", "SG"}, exclude=None, resolved=True))
        self.assertTrue(country_allowed("SG", allow={"US", "SG"}, exclude=None, resolved=True))
        self.assertTrue(country_allowed("us", allow={"US", "SG"}, exclude=None, resolved=True))
        self.assertTrue(country_allowed("sg", allow={"US", "SG"}, exclude=None, resolved=True))

        # Countries not in allow list
        self.assertFalse(country_allowed("DE", allow={"US", "SG"}, exclude=None, resolved=True))
        self.assertFalse(country_allowed("ID", allow={"US", "SG"}, exclude=None, resolved=True))
        self.assertFalse(country_allowed("", allow={"US", "SG"}, exclude=None, resolved=True))
        self.assertFalse(country_allowed(None, allow={"US", "SG"}, exclude=None, resolved=True))

    def test_resolved_allow_and_exclude_combination(self) -> None:
        """Exclude takes priority if both contain the country code."""
        # Conflicting configuration: exclude takes precedence
        self.assertFalse(country_allowed("ID", allow={"US", "ID"}, exclude={"ID"}, resolved=True))
        self.assertTrue(country_allowed("US", allow={"US", "ID"}, exclude={"ID"}, resolved=True))

    def test_resolved_unconstrained(self) -> None:
        """When neither allow nor exclude is specified: all pass."""
        self.assertTrue(country_allowed("ID", allow=None, exclude=None, resolved=True))
        self.assertTrue(country_allowed("US", allow=None, exclude=None, resolved=True))
        self.assertTrue(country_allowed(None, allow=None, exclude=None, resolved=True))


class TestThkConfig(unittest.TestCase):
    """Verify that TokenHarbor egress configuration excludes ID."""

    def test_thk_config_excludes_id(self) -> None:
        self.assertIn("thk", EGRESS_COUNTRY)
        thk_rules = EGRESS_COUNTRY["thk"]
        self.assertIn("exclude", thk_rules)
        exclude_set = {str(c).upper() for c in thk_rules["exclude"]}
        self.assertIn("ID", exclude_set)


class TestSignatureAcceptance(unittest.TestCase):
    """Verify function signatures accept countries parameters."""

    def test_validate_signature(self) -> None:
        sig = inspect.signature(validate)
        self.assertIn("countries", sig.parameters)
        self.assertIn("exclude_countries", sig.parameters)
        # Test call with country params
        res = validate([], target=1, countries={"US"}, exclude_countries={"ID"})
        self.assertEqual(res, [])

    def test_auto_egress_signature(self) -> None:
        sig = inspect.signature(auto_egress)
        self.assertIn("country", sig.parameters)
        self.assertIn("exclude_countries", sig.parameters)


class TestCLIArgparse(unittest.TestCase):
    """Verify CLI parser for thk commands exposes --country."""

    def test_thk_batch_parser_has_country(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["thk", "batch", "1", "--country", "US"])
        self.assertEqual(getattr(args, "country", None), "US")

    def test_thk_batch_default_country_is_none(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["thk", "batch", "1"])
        self.assertIsNone(getattr(args, "country", None))


if __name__ == "__main__":
    unittest.main()
