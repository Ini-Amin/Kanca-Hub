"""
tests/test_thk_action_prefix.py — Pin the TokenHarbor server-action wire format.

WHY THIS EXISTS
---------------
On 2026-10-19 the farm created 0 accounts and reported
`Signup failed: Email and password are required.` The cause was not validation:
`_build_multipart_body` prefixed every field with a bare `1_`, but a Next.js
bound server action prefixes them with `_<formIndex>_`. The action still
resolved, so the server answered with a *validation* error while every field
arrived empty -- a silent, total outage that looked like a bad email.

The golden body below is a verbatim capture of what a real browser sends
(camoufox, POST https://tokenharbor.ai/login?mode=signup), so this test fails
if the encoding drifts again in either direction: a missing/extra field, a
wrong prefix, or a changed control-field value.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HARBOR_DIR = REPO_ROOT / "harbor"
if str(HARBOR_DIR) not in sys.path:
    sys.path.insert(0, str(HARBOR_DIR))

from tools.tokenharbor.client import TokenHarborClient  # noqa: E402

# Field order and names exactly as a real browser sends them. The browser body
# has no cf-turnstile-response (the capture did not complete the challenge), so
# the builder's extra field is inserted where the real form would carry it.
BROWSER_FIELD_ORDER = [
    "_1_$ACTION_REF_1",
    "_1_$ACTION_1:0",
    "_1_$ACTION_1:1",
    "_1_$ACTION_KEY",
    "_1_device_fingerprint",
    "_1_timezone",
    "_1_next",
    "_1_cf-turnstile-response",
    "_1_email",
    "_1_password",
    "_1_invite_code",
    "0",
]

ACTION_HASH = "60e3618e7b6601f4077e38c6eb8d9dd7704859a130"


def _field_names(body: bytes) -> list[str]:
    return re.findall(r'name="([^"]+)"', body.decode("utf-8"))


def _field_value(body: bytes, name: str) -> str:
    text = body.decode("utf-8")
    m = re.search(
        r'name="%s"\r\n\r\n(.*?)\r\n' % re.escape(name), text, re.DOTALL
    )
    return m.group(1) if m else ""


class TestServerActionWireFormat(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TokenHarborClient()

    def _body(self) -> bytes:
        body, _ = self.client._build_multipart_body(
            ACTION_HASH,
            [
                ("cf-turnstile-response", "TOKEN"),
                ("email", "user@example.com"),
                ("password", "Sup3rSecret!123"),
                ("invite_code", ""),
            ],
        )
        return body

    def test_field_names_and_order_match_browser(self) -> None:
        self.assertEqual(_field_names(self._body()), BROWSER_FIELD_ORDER)

    def test_no_bare_1_prefix(self) -> None:
        """The regression itself: a field named '1_email' means the bug is back."""
        names = _field_names(self._body())
        self.assertNotIn("1_email", names)
        self.assertNotIn("1_$ACTION_REF_1", names)
        for n in names:
            if n != "0":
                self.assertTrue(
                    n.startswith(self.client._FORM_PREFIX),
                    f"{n!r} lacks the {self.client._FORM_PREFIX!r} prefix",
                )

    def test_bound_action_metadata_is_intact(self) -> None:
        body = self._body()
        self.assertEqual(
            _field_value(body, "_1_$ACTION_1:0"),
            '{"id":"%s","bound":"$@1"}' % ACTION_HASH,
        )
        self.assertEqual(_field_value(body, "_1_$ACTION_1:1"), '["$undefined"]')
        self.assertEqual(_field_value(body, "0"), '["$undefined","$K1"]')

    def test_credentials_land_under_the_prefixed_names(self) -> None:
        body = self._body()
        self.assertEqual(_field_value(body, "_1_email"), "user@example.com")
        self.assertEqual(_field_value(body, "_1_password"), "Sup3rSecret!123")
        self.assertEqual(_field_value(body, "_1_cf-turnstile-response"), "TOKEN")

    def test_action_key_is_fresh_per_body(self) -> None:
        a = _field_value(self._body(), "_1_$ACTION_KEY")
        b = _field_value(self._body(), "_1_$ACTION_KEY")
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("k"))


if __name__ == "__main__":
    unittest.main()