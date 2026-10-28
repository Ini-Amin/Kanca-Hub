"""No shared-brand identity anywhere: farms must never mint `kanca`-prefixed
account names/usernames (that prefix fingerprints every account as one owner).

Covers persona.py itself plus every farm that used to hardcode the prefix.
"""
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

SAFE = re.compile(r"^[A-Za-z0-9_]+$")


class TestNoBrandIdentity(unittest.TestCase):
    def test_persona_names(self):
        import persona
        for _ in range(200):
            self.assertTrue(SAFE.match(persona.display_name()))
            self.assertNotIn("kanca", persona.display_name().lower())
            self.assertNotIn("kanca", persona.full_name().lower())
            self.assertNotIn("kanca", persona.username().lower())

    def test_username_length(self):
        import persona
        for _ in range(50):
            u = persona.username(max_len=20)
            self.assertLessEqual(len(u), 20)
            self.assertTrue(SAFE.match(u))

    def test_password_shape(self):
        import persona
        for _ in range(20):
            p = persona.password(14)
            self.assertGreaterEqual(len(p), 8)
            self.assertTrue(any(c.isdigit() for c in p))

    def test_no_kanca_prefix_in_farms(self):
        import inspect
        import github_bd_signup
        import github_to_anything
        import windscribe_account_farm
        import autofarm

        def code(fn):
            # strip comments: we care about what the code DOES, not what it explains
            return "\n".join(
                ln for ln in inspect.getsource(fn).splitlines()
                if not ln.strip().startswith("#"))

        for fn in (github_bd_signup._username, github_to_anything.gen_password,
                   autofarm._persona_name_parts):
            self.assertNotIn('"kanca"', code(fn).lower())
        self.assertIn("persona", inspect.getsource(windscribe_account_farm.create_account))
        self.assertIn("persona", inspect.getsource(github_to_anything.gen_password))
        for _ in range(50):
            for v in (github_bd_signup._username(), github_to_anything.gen_password()):
                self.assertNotIn("kanca", v.lower())

    def test_no_hardcoded_brand_fill(self):
        import inspect
        import github_to_anything
        import autofarm
        src = inspect.getsource(github_to_anything) + inspect.getsource(autofarm)
        self.assertNotIn('.fill("Kanca', src)
        self.assertNotIn(".fill('Kanca", src)
        self.assertNotIn('f"Kanca_', src)
        self.assertNotIn('f"kanca', src)


if __name__ == "__main__":
    unittest.main()
