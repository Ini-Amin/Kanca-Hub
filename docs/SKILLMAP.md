# SKILLMAP — which skill for which task (read this, don't guess)

142 skills live in two trees that opencode MERGES at load:
`~/.config/opencode/skills` (72, general/UI) + `~/.claude/skills` (70, pentest).
Same name in both = identical copy (17 of them: cloudflare*, basin, k2,
wrangler, luvus, sandbox-*, nextjs, turnstile, web-perf, agents-sdk).
Harmless duplication — do NOT "fix" by deleting one side; they drift.

Rule: for each task below, use PRIMARY. Use BACKUP only if primary fails.
NEVER use anything in the FORBIDDEN column — it is a worse twin, not a choice.

## Bug bounty pipeline (in order — do not skip steps)

| Step | PRIMARY | BACKUP | FORBIDDEN (why) |
|---|---|---|---|
| Orient (start/switch/lost) | `bb-methodology` | — | `hunting-methodology` (no description; agent can't trigger it) |
| Workspace + scope | `new`, then `sync` (platform scope), `brain` init | Bugcrowd: researcher API token NOT available for new-platform accounts (tracker.bugcrowd.com has a separate user store; `unable to sign in` is expected) — sync scope via web fetch instead. API v1.2.0 docs kept as reference only: https://docs.bugcrowd.com/api/1.2.0/ | — |
| Recon | `pipeline` → `web2-recon` → `surface` | `quickscan` (shallow), `mindmap` (visual) | `recon-methodology` (no description) |
| Hunt generic | `hunt` | `autopilot` (long exhaustion run), `fullscan` (brain-coordinated) | mixing all three at once (different depths; pick ONE per session) |
| Hunt by class | `hunt-idor/xss/rce/oauth/llm-ai/business-logic/info-disclosure` | `graphql-audit`, `mobile-pentest`, `web3-audit`, `meme-coin-audit`, `cicd-security` | — (these are siblings, not rivals) |
| Payloads/bypass | `security-arsenal` (consult, don't "run") | `argus` (scanner suite), `web2-vuln-classes` (reference) | `vuln-classes` (no description) |
| Chain findings | `chain` (deep, one bug) | `correlate` (many findings) | running both on the same input |
| Validate | `validate` (one) / `triage` (batch) — BOTH execute the gate in `triage-validation` | — | reading `triage-validation` as a runner (it is the SPEC, not the runner) |
| CVE/CWE lookup | `cve.db` index (`~/.config/auto-freecf/cve.db`, built by `scripts/build_cve_index.py`) | web search | grepping 400K raw NVD files |
| Dupcheck | `dupcheck` | platform hacktivity | skipping it (dup = wasted report) |
| Report | `report` (generates) + `report-writing` (style guide) — complementary, use BOTH in this order | — | `report` alone (tone gets rejected) |
| Quality gate | `quality` (score before submit) | — | submitting below gate |
| Submit | `submit` | — | — |
| Learn/loop | `learn`, `remember`, `cost`, `status`, `monitor`, `resume` | — | — |
| Client-side signing blocks replay | `client-reverse` | — | — |
| Password spray decision | `credential-attack` | — | spraying without reading it first (legal guardrails inside) |

## Pre-submit gates (ALL FOUR block submit — measured 2026-10-16: skipping
them cost -10 reputation: duplicates + excluded-scope work + untested PoC)

| # | Gate | What kills the finding | Tool |
|---|---|---|---|
| 1 | SCOPE | target in program's excluded list, or outside scope.yaml | `sync` scope first, then match; excluded = instant kill, no appeal |
| 2 | DUPCHECK | same/similar vuln already in hacktivity or local findings | `dupcheck` + `cve.db` FTS; weak-model drafts (deepseek-class) are dup-prone — never submit one without this gate |
| 3 | VM-PoC | no working proof-of-concept in a disposable VM | `blu` sandbox (ephemeral by design); untested = not a finding |
| 4 | CHALLENGER | different-family model can't break it | planner/drafter/challenger split (see AGENTS.md bounty roles) |

Only after 4/4 pass: `report` → `report-writing` → `quality` → Telegram draft → human submits. No gate may be skipped "because the model is confident".

## Website gigs

| Task | PRIMARY | BACKUP | FORBIDDEN (why) |
|---|---|---|---|
| Design reference | `mkdir.directory` list (appendix below) + `apple-design` + `emil-design-eng` | `siteinspire/awwwards` direct | designing from blank prompt |
| Prototype UI | `prototype-ui` (visual picker, variants) | — | `prototype` (generic throwaway, no picker) |
| Motion new | `animate` (+ `animate-expo` for RN) | — | — |
| Motion review/fix | `review-animations` → `improve-animations` → `find-animation-opportunities` | `animation-performance` (jank only) | running all four at once |
| Break/edge cases | `break-ui` | — | — |
| Perf | `web-perf` | — | — |
| Anti-slop (always on) | `antislop` core + `antislop-ui/code/copywriting/human/layoutmobile` as needed | — | skipping core |
| Mobile feel | `mobile-native` | — | — |
| Copy/text | `antislop-copywriting` | — | — |

## Code review / SAST (bounty + gigs)

| Task | PRIMARY |
|---|---|
| SAST pass | `sast` (+ `sast-methodology` is its spec — read, don't run) |
| Scanner | `opengrep` binary (`~/.local/bin`, v1.31.0) + `security-arsenal` grep patterns |
| Human review of a diff | `code-review`, `ponytail-review` |
| Diagnose bug/perf | `diagnosing-bugs` |

## Engagement management (do not confuse with hunting)

`brain`/`status`/`cost` = state. `new`/`resume`/`monitor`/`sync` = lifecycle.
`submit`/`report`/`quality` = output. Never substitute one group for another.

## Grill (challenger pattern for plans)

`grilling` (primary). `grill-me`/`grill-with-docs` are older twins — same
job, pick `grilling` unless docs output is explicitly wanted.

## Appendix: design reference (mkdir.directory top picks for gig work)

Landing/SaaS: Saaspo, SaaS Landing Page, Land-book, Unsection (sections).
Awwwards-level: Awwwards, FWA, CSS Design Awards, Siteinspire, Inspora.
Motion: GSAP Showcase, Lenis Showcase, Movin. Details: Footer.design,
Navbar Gallery, CollectUI, Minimal Gallery. Full ranked list of 31 at
https://mkdir.directory/ (all free, no account).
