"""Pure tests for scripts/ai_key_hunt.py ranking/dedupe (no network)."""
from __future__ import annotations
import sys
from pathlib import Path
import unittest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts"))
import ai_key_hunt as H  # noqa: E402


class TestDomain(unittest.TestCase):
    def test_domain_of(self):
        self.assertEqual(H.domain_of("https://sub.Example.com/x"), "sub.example.com")
        self.assertEqual(H.domain_of(""), "")


class TestScore(unittest.TestCase):
    def test_free_key_beats_junk(self):
        good = {"title": "Free LLM API key", "description": "no credit card required, sign up"}
        junk = {"title": "Instagram reel", "description": "bansos ai vibecoding"}
        self.assertGreater(H.score_hit(good), H.score_hit(junk))


class TestRankDedupe(unittest.TestCase):
    def test_dedupe_by_domain_and_rank(self):
        hits = [
            {"url": "https://a.com/1", "title": "x"},
            {"url": "https://a.com/2", "title": "free api key submit key"},  # dup domain
            {"url": "https://b.com", "title": "free tier no credit card api key"},
        ]
        out = H.rank_and_dedupe(hits, seen=set())
        self.assertEqual([h["domain"] for h in out], ["b.com", "a.com"])  # ranked, deduped

    def test_seen_dropped(self):
        hits = [{"url": "https://a.com", "title": "free api key"}]
        self.assertEqual(H.rank_and_dedupe(hits, seen={"a.com"}), [])


if __name__ == "__main__":
    unittest.main()


class TestCuratedSeed(unittest.TestCase):
    def test_curated_domains_parsed_from_doc(self):
        d = H.curated_domains()
        self.assertIn("bazaarlink.ai", d)
        self.assertIn("bansos.dev", d)

    def test_curated_domains_are_skipped(self):
        hits = [{"url": "https://bazaarlink.ai/free", "title": "free api key no credit card"},
                {"url": "https://brand-new-site.example", "title": "free llm api key signup"}]
        seen = H._load_seen()  # includes curated
        out = H.rank_and_dedupe(hits, seen)
        self.assertEqual([h["domain"] for h in out], ["brand-new-site.example"])
