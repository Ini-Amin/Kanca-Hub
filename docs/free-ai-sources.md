# Free-AI-key sources (curated) — read this BEFORE scraping

Purpose: stop new users from burning Firecrawl/TinyFish quota re-discovering the
same sites. This is the frozen result of `kancahub hunt`. Update it when you find
something new; `kancahub hunt` seeds from this file first, then only scrapes for
what's NOT already here.

Last curated: 2026-10-08 (first pass). Sources: Firecrawl search + community
(list below), not affiliated/endorsed.

## A. Direct free-key providers (self-serve)

| Site | URL | What | Caveat |
|---|---|---|---|
| **BazaarLink** | https://bazaarlink.ai/free | free LLM API, 2 models, no card | **shared free pool → `free_global_rate_limited` (site-wide full), not your quota.** Flaky by design. Already in our 9Router. |
| **OrcaRouter** | https://www.orcarouter.ai/offers | free AI API credits/models | verify terms; may need signup |
| **Webshare** (proxy, not AI) | proxy.webshare.io/register | 10 free proxies/mo | for egress, not keys |

## B. Aggregator lists (find many keys in one page — scrape once, cache here)

| Site | URL | Note |
|---|---|---|
| GitHub `OuterSpacee/free-ai-apis` | https://github.com/OuterSpacee/free-ai-apis | curated free-API table (Image/LLM/etc.) |
| Reddit r/SideProject | (thread) "…don't need a credit card to start building with AI APIs" | community list |
| Medium "4 Free AI APIs" | shaktiwadekar.medium.com/… | intro list |

## C. Indonesian "bansos AI" lists (the community's own term)

| Site | URL | Note |
|---|---|---|
| **Bansos.dev** | https://bansos.dev/list/ | ⭐ community list — **user's main source** |
| AppVerse | https://appverse.id/bansos-ai | "Kumpulan Bansos AI Murah dan Gratis" |
| GutsAI | https://gutsai.id/bansos | bansos list |

## D. Where to *discuss/find* new bansos (human feeds, not clean scrape targets)

- **Threads** — often has **scripts + fresh info** before any list. Tips:
  - `q=aithreads` is LOW signal (returns general social posts). Use the account/query
    **"bansos"**, **"bansos ai"**, **"free model"**, **"openagentic"** instead.
  - Scrape-averse (Meta anti-bot); works via an unblocker (Bright Data) but noisy —
    treat as a discovery feed, extract leads by hand.
- **`@openagentic.id`** (Threads) — lead: "Bansos gratis akses model GPT-6 Sol
  (Codex), daily shared pool 500M tokens, resets 00:00" — buy any plan → free model
  access. ⚠ not verified by us; check terms.
- Telegram/Discord communities linked from the sites above.

## E. Why bansos pools are flaky (read before judging a key "dead")

BazaarLink returns `free_global_rate_limited`: *"The site-wide free-model capacity
is currently full. This is not your personal quota."* → free pools are **shared and
saturate site-wide**, so a 429/503 often means "try later", **not** "your key is
invalid". Always **verify before inject** and don't hoard keys.

## How to use

1. **Read this file first.** Only scrape what's missing.
2. `kancahub hunt discover --query "<new keyword>"` — only when adding fresh leads.
3. Verify a key before trusting it: `kancahub hunt` / `thk test-key` / `diagnose`.
4. When you find a new site, **add a row here** — that's the whole point: the next
   user spends zero quota.

## Anti-treadmill note
A "bansos" list is a means, not the goal. Collecting keys is not the work. Take
ONE that works, route your real task through it, and stop.

## F. Reference tools (not vendors)
- **0x-cookies** (github.com/0xgetz/0x-cookies) — OpenBullet-style **cookie/session
  checker** (139 platforms, headless Chromium for real logged-in verdicts). Useful
  pattern for validating saved sessions/keys (overlaps our test-key/verify), but it
  is NOT a SheerID tool and has no SheerID code.
- SheerID doc-upload is **browser-only + server-reviewed** (anti-fraud). No public
  or safe API to POST a document; our k12 flow finds the link + opens the flow, and
  the upload/review is the parked human gate.
