"""Regression: the RLHF-style slider loop must turn passes/labels into a
reusable, content-hash keyed label store, and the solver must consult it first.

The point of the loop: every PASS is a free label (the target that landed IS the
truth), and every human correction in the review page is a label. Both must land
in a store the solver reads BEFORE any heuristic, so a known puzzle is answered
for free and exactly. This guards:
  * slider_attempts.mine_labels() -> slider_labels_store.json (sha256(bg) -> x)
  * captcha_slider.gap_by_label() resolves a known image, and refuses out-of-range
"""
import base64
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000100ffff03000006000557bfabd4000000"
    "0049454e44ae426082")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestLabelStoreMining(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.data = self.tmp / "slider_data"
        self.data.mkdir()
        self.dataset = self.tmp / "dataset.jsonl"
        self.mine_out = self.tmp / "mined.json"
        self.store = self.tmp / "labels_store.json"
        self.verified = self.tmp / "verified.json"
        # two puzzle images; only one of them is a real pass in the dataset
        (self.data / "aaa_bg.png").write_bytes(PNG)
        (self.data / "aaa_piece.png").write_bytes(PNG)
        (self.data / "bbb_bg.png").write_bytes(PNG)
        (self.data / "bbb_piece.png").write_bytes(PNG)
        rows = [
            {"passed": True, "target": 137,
             "images": {"bg": "aaa_bg.png", "piece": "aaa_piece.png"}},
            {"passed": False, "target": 150,
             "images": {"bg": "bbb_bg.png", "piece": "bbb_piece.png"}},
        ]
        self.dataset.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        # a human-verified label that must WIN over anything mined
        self.verified.write_text(json.dumps({"bbb": {"x": 88}}))

    def test_mine_writes_content_hash_store(self):
        sa = _load("slider_attempts", SCRIPTS / "slider_attempts.py")
        sa.LIVE_LOG = self.dataset
        sa.VERIFIED_PATH = self.verified
        sa.STORE_PATH = self.store
        truth = sa.mine_labels(dataset=self.dataset, out=self.mine_out,
                               store=self.store, data_dir=self.data)
        # mined pass + verified label both present
        self.assertEqual(truth["aaa"], 137)
        self.assertEqual(truth["bbb"], 88)
        store = json.loads(self.store.read_text())
        h = hashlib.sha256(PNG).hexdigest()[:16]
        self.assertIn(h, store)
        self.assertEqual(store[h]["x"], 88)  # verified wins over the failed row

    def test_gap_by_label_resolves_and_bounds(self):
        cs = _load("captcha_slider", SCRIPTS / "captcha_slider.py")
        cs.DATA_DIR = self.data
        h = hashlib.sha256(PNG).hexdigest()[:16]
        (self.data.parent / "slider_labels_store.json").write_text(
            json.dumps({h: {"x": 123}}))
        cs._LABEL_STORE_CACHE = None
        b64 = base64.b64encode(PNG).decode()
        self.assertEqual(cs.gap_by_label(b64, b64), 123)
        # out-of-range values are refused (never drag past the canvas)
        (self.data.parent / "slider_labels_store.json").write_text(
            json.dumps({h: {"x": 9999}}))
        cs._LABEL_STORE_CACHE = None
        self.assertIsNone(cs.gap_by_label(b64, b64))

    def test_vision_off_by_default(self):
        cs = _load("captcha_slider", SCRIPTS / "captcha_slider.py")
        # the free pool vision models answer a round constant -> must be OFF
        self.assertEqual(cs.VISION_MODEL, "")

    def test_gap_range_covers_low_x(self):
        cs = _load("captcha_slider", SCRIPTS / "captcha_slider.py")
        # the 41 user labels run 15..240; the old [100,300] clamp threw low-X away
        self.assertLessEqual(cs.GAP_MIN, 15)
        self.assertGreaterEqual(cs.GAP_MAX, 240)


if __name__ == "__main__":
    unittest.main()


class TestClaimOrigin(unittest.TestCase):
    """The claim flow must mount the Aliyun widget on the REAL origin
    (chat.z.ai/auth), not zcode.z.ai/ (which 307s to a marketing page and made
    the claim fail with 3007 captcha-verify-failed).

    zcode_claim imports playwright at module scope (only present in the browser
    venv), so assert on the source text rather than importing it."""

    def _src(self):
        return (SCRIPTS / "zcode_claim.py").read_text()

    def test_claim_origin_is_chat_z_ai_auth(self):
        src = self._src()
        self.assertIn('os.environ.get("ZC_CLAIM_ORIGIN", "https://chat.z.ai/auth")', src)
        # the old marketing origin must not be the goto target anymore
        self.assertNotIn('page.goto("https://zcode.z.ai/"', src)
        self.assertIn("await page.goto(CLAIM_ORIGIN", src)
