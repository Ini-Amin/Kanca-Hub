#!/usr/bin/env python3
"""
scripts/ai_key_hunt.py — hunt for free-AI-key sites by keyword, prove ONE, then
scale only if you say so.

This is the anti-treadmill design: discover -> extract -> prioritise sites that
expose an EMAIL signup -> get ONE key -> VERIFY it works -> inject/store into
9Router -> *then* ask "bulk or sequential?" with a pace. Nothing unverified is
ever injected (that's what made "keys go invalid" before).

Stages:
  1. discover  — Firecrawl search (queries: "free ai", "bansos ai", …)
  2. extract   — read each hit, keep ones that look like free-key signups
  3. prioritise— rank sites that offer an email/API-key signup above others
  4. warm-up   — you take ONE; we test it; only then offer bulk/sequential

Usage:
  python scripts/ai_key_hunt.py discover --query "free ai api key no credit card" [--limit 10]
  python scripts/ai_key_hunt.py discover --queries "free ai,bansos ai,free llm api"
  python scripts/ai_key_hunt.py report                 # show last discovery
  (then interactively) run --bulk / --sequential with --pace
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
CFG = Path.home() / ".config" / "auto-freecf"
SEEN = CFG / "ai_hunt_seen.json"          # domains we've already surfaced (dedupe)
LAST = CFG / "ai_hunt_last.json"          # last discovery result

DEFAULT_QUERIES = ["free ai api key", "free ai no credit card", "bansos ai", "free llm api key"]
# signals a site actually hands out a free key
KEY_HINTS = ("api key", "apikey", "api-key", "free tier", "free trial", "no credit card",
             "token", "generate key", "get key", "sign up", "signup", "register")
EMAIL_HINTS = ("email", "sign up with email", "create account", "register")


# ── pure helpers (testable) ──────────────────────────────────────────
def domain_of(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url or "")
    return (m.group(1) if m else "").lower().lstrip("www.")


def score_hit(hit: dict) -> int:
    """Higher = more likely a farmable free-key signup. Pure."""
    text = f"{hit.get('title','')} {hit.get('description','')} {hit.get('markdown','')}".lower()
    s = 0
    s += sum(3 for h in ("free tier", "no credit card", "api key") if h in text)
    s += sum(1 for h in KEY_HINTS if h in text)
    s += 2 if any(e in text for e in EMAIL_HINTS) else 0
    if any(x in text for x in ("free trial", "sign up", "signup", "register")):
        s += 2
    return s


def rank_and_dedupe(hits: list[dict], seen: set[str]) -> list[dict]:
    """Unique by domain, drop already-seen, highest score first. Pure."""
    out, used = [], set()
    for h in hits:
        d = domain_of(h.get("url", ""))
        if not d or d in seen or d in used:
            continue
        used.add(d)
        h = {**h, "domain": d, "score": score_hit(h)}
        out.append(h)
    return sorted(out, key=lambda h: h["score"], reverse=True)


# ── io ───────────────────────────────────────────────────────────────
def _load_seen() -> set[str]:
    """Domains we've already surfaced = seen-list PLUS the curated docs list, so
    a new user never re-scrapes (and re-pays quota for) known sites."""
    seen: set[str] = set()
    try:
        seen |= set(json.loads(SEEN.read_text()))
    except Exception:
        pass
    seen |= curated_domains()
    return seen


def curated_domains() -> set[str]:
    """Parse docs/free-ai-sources.md for already-known domains/URLs."""
    doc = Path(__file__).resolve().parent.parent / "docs" / "free-ai-sources.md"
    try:
        text = doc.read_text()
    except Exception:
        return set()
    doms = set()
    for m in re.finditer(r"https?://([^\s)/|]+)", text):
        doms.add(m.group(1).lower().lstrip("www."))
    return doms


def discover(queries: list[str], limit: int) -> list[dict]:
    import firecrawl
    hits: list[dict] = []
    for q in queries:
        try:
            res = firecrawl.search(q, limit=limit)
        except Exception as e:
            print(f"  [hunt] search '{q}' failed: {str(e)[:60]}")
            continue
        items = res.get("data") or []
        if isinstance(items, dict):
            items = items.get("web") or []
        for it in items:
            if isinstance(it, dict):
                hits.append(it)
        print(f"  [hunt] '{q}' -> {len(items)} hits")
    seen = _load_seen()
    ranked = rank_and_dedupe(hits, seen)
    return ranked


def save_discovery(ranked: list[dict]) -> None:
    CFG.mkdir(parents=True, exist_ok=True)
    LAST.write_text(json.dumps(ranked, indent=2))
    seen = _load_seen() | {h["domain"] for h in ranked}
    SEEN.write_text(json.dumps(sorted(seen)))


def report() -> int:
    try:
        ranked = json.loads(LAST.read_text())
    except Exception:
        print("  no discovery yet — run: ai_key_hunt discover --query '...'"); return 1
    print(f"  {len(ranked)} ranked candidate(s):\n")
    for i, h in enumerate(ranked, 1):
        print(f"  [{i}] score={h.get('score'):>3}  {h.get('domain')}")
        print(f"      {h.get('title','')[:70]}")
        print(f"      {h.get('url','')}")
    print("\n  next: pick one, open it, get a key; then verify + inject.")
    return 0


def ask_scale() -> str:
    """The earn-its-keep step: prove ONE, then choose bulk/sequential/stop."""
    print("\n  ✓ one key proven + injected.")
    print("    [b] bulk      — farm as many as possible across the queue")
    print("    [s] sequential— one at a time, pace between each")
    print("    [q] stop      — keep the one and stop (anti-treadmill default)")
    try:
        return (input("  choose [b/s/q] (Enter=q): ") or "q").strip().lower()[:1] or "q"
    except (EOFError, KeyboardInterrupt):
        return "q"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Hunt free-AI-key sites by keyword; prove ONE, then scale")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("discover"); d.add_argument("--query", default=None)
    d.add_argument("--queries", default=None); d.add_argument("--limit", type=int, default=8)
    sub.add_parser("report")
    sub.add_parser("ask-scale")
    a = ap.parse_args(argv)
    if a.cmd == "report":
        return report()
    if a.cmd == "ask-scale":
        print("  choice:", ask_scale()); return 0
    queries = ([a.query] if a.query else (a.queries.split(",") if a.queries else DEFAULT_QUERIES))
    ranked = discover([q.strip() for q in queries if q.strip()], a.limit)
    save_discovery(ranked)
    print(f"\n  ✓ {len(ranked)} fresh candidate(s) saved ({LAST})")
    return report()


if __name__ == "__main__":
    sys.exit(main())