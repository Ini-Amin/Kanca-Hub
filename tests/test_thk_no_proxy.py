"""Regression: kancahub thk --no-proxy must make harbor go DIRECT (env override)."""
from __future__ import annotations
from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))


class TestHarborNoProxyEnv(unittest.TestCase):
    def test_harbor_cli_respects_no_proxy_env(self):
        src = (Path.home() / "harbor" / "tools" / "tokenharbor" / "cli.py").read_text()
        self.assertIn("TOKENHARBOR_NO_PROXY", src,
                      "harbor must honor TOKENHARBOR_NO_PROXY to force direct egress")

    def test_kancahub_sets_no_proxy_on_direct(self):
        src = (REPO / "scripts" / "kancahub.py").read_text()
        self.assertIn('env["TOKENHARBOR_NO_PROXY"] = "1"', src,
                      "cmd_thk must set TOKENHARBOR_NO_PROXY when egress is direct")


if __name__ == "__main__":
    unittest.main()
