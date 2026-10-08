"""Pure-helper tests for scripts/residential_proxy_signup.py (no browser/network)."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
import unittest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

import residential_proxy_signup as R  # noqa: E402


class TestGenerators(unittest.TestCase):
    def test_email_shape(self):
        e = R.generate_email("kancalabs.biz.id", rand_suffix="abc123")
        self.assertEqual(e, "rpabc123@kancalabs.biz.id")

    def test_password_compliance(self):
        p = R.generate_password()
        self.assertGreaterEqual(len(p), 10)
        self.assertTrue(any(c in "!@#$%^&*" for c in p))
        self.assertTrue(any(c.isdigit() for c in p))


class TestFindCode(unittest.TestCase):
    def test_prefers_vendor_branded_mail(self):
        mails = [
            {"subject": "Welcome", "text": "no code here 999"},
            {"subject": "RapidProxy code", "text": "Your code is 123456"},
        ]
        self.assertEqual(R.find_code(mails, vendor="rapidproxy"), "123456")

    def test_any_code_when_not_branded(self):
        self.assertEqual(R.find_code([{"text": "code 4321"}]), "4321")

    def test_none(self):
        self.assertIsNone(R.find_code([{"text": "hello"}]))


class TestParseProxies(unittest.TestCase):
    def test_full_quad_csv(self):
        got = R.parse_proxies("1.2.3.4:8080:user:pass\n")
        self.assertEqual(got, ["http://user:pass@1.2.3.4:8080"])

    def test_bare_ip_port(self):
        self.assertEqual(R.parse_proxies("1.2.3.4:8080"), ["1.2.3.4:8080"])

    def test_skips_headers_and_junk(self):
        text = "# note\nhostname:port:username:password\nhello world\n5.6.7.8:9090:u:p"
        self.assertEqual(R.parse_proxies(text), ["http://u:p@5.6.7.8:9090"])


class TestAppendToFile(unittest.TestCase):
    def test_dedupes(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "pool.txt"
            self.assertEqual(R.append_to_file(["a", "b"], p), 2)
            self.assertEqual(R.append_to_file(["b", "c"], p), 1)
            self.assertEqual(p.read_text().splitlines(), ["a", "b", "c"])


class TestSites(unittest.TestCase):
    def test_vendors_have_urls(self):
        for name, site in R.SITES.items():
            for key in ("register", "login", "dashboard", "default_out"):
                self.assertIn(key, site, f"{name} missing {key}")
            self.assertTrue(str(site["register"]).startswith("https://"))


if __name__ == "__main__":
    unittest.main()