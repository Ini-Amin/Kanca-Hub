"""Regression: grok_driver maps a single --proxy to a VALID proxy_mode."""
from __future__ import annotations
from pathlib import Path
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

import grok_driver

VALID = {"auto", "direct", "single", "pool"}


class TestGrokProxyMode(unittest.TestCase):
    def test_single_proxy_uses_valid_mode(self):
        cfg = grok_driver.prepare_config(accounts=1, workers=1, proxy="http://127.0.0.1:8888")
        self.assertEqual(cfg.get("proxy_mode"), "single")
        self.assertIn(cfg.get("proxy_mode"), VALID)

    def test_pool_uses_pool_mode(self):
        cfg = grok_driver.prepare_config(accounts=1, workers=1, proxy_pool="/tmp/none.txt")
        self.assertIn(cfg.get("proxy_mode"), VALID)


if __name__ == "__main__":
    unittest.main()
