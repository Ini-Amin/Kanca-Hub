# Gig platforms (distilled from gigs.sh 46 verified, 2026-10-14)

> Source: `https://gigs.sh/api/v1/gigs` (full dump in
> `~/.config/auto-freecf/logs/gigs_all.json`). Tiers by agent-fit for US:
> no-KYC + USDC + instant onboarding first. Status changes — re-pull before
> trusting any single row.

## Tier 0 — registered and working
- **Superteam Earn** — agent `KancaLabs` registered, apiKey in `.env`,
  claimCode with owner. AGENT_ALLOWED/AGENT_ONLY listings, USDC, human claims.

## Tier 1 — agent-welcomed, no KYC, USDC, instant/easy onboarding
- **Agent Hansa** (quest marketplace, instant, `llms-full.txt` was unreachable
  2026-10-14 — retry before building), **Clustly** ($40–240 tasks),
  **Daydreams TaskMarket**, **AgentHire** ($50K+ volume claimed),
  **AgenticTrade** (per-call), **AgentPact**, **Agoragentic** ($0.10+/call),
  **NEAR market** (~$24 jobs), **Skyfire** (per-API-call).
- Next action: pick ONE, self-onboard (POST register), take a small task.

## Tier 2 — GitHub bounties (PR = submission, fits our sandbox+review flow)
- **Opire** (bot on OSS issues, Stripe, 4% fee), **boss.dev**, **Dework**
  (DAO bounties, wallet, no KYC), **Algora** (bounty API EMPTY as of
  2026-10-14 — recheck before relying).

## Tier 3 — security bounties (high pay, KYC at payout, needs accounts+rep)
- **huntr** (AI/ML jailbreak challenges — fits hunt-llm-ai skill),
  **HackerOne**, **Bugcrowd**, **Intigriti**, **Immunefi**, **Sherlock**
  ($19M+ paid), **HackenProof**, Google bughunters. Anti-slop gates apply:
  sandbox tests green + ≤3 competing attempts + Telegram approve before PR.

## Tier 4 — competitions (periodic, high variance)
- **Kaggle/ARC** ($1M+ pools), **Devpost**, **lablab.ai**, **ETHGlobal**,
  **Encode**, **Topcoder** (tolerated), **Zindi**, **DrivenData**, **AICrowd**.

## Tier 5 — creator payouts (the content plan's money layer)
- **YouTube Partner** ($100 min), **TikTok creators**, **Spotify**, **Substack**,
  **Paragraph**, **Stacker News** (Lightning), **Reddit contributor**.
- Micropayment rails: **x402/the402**, **BountyBook**, **Claw Earn**, **Drips**.

## Pipeline mapping (Gemini doc vs ours)
Triage/Scout = gig_finder + agent endpoints. Planner/Executor = subagents.
Reviewer = sandbox tests + ≤3-attempt filter + Telegram gate (>$20).
Curator = publish good artifacts (Apify/MuleRun later). Memory = memory.py
pgvector. All pieces exist; no dashboard — chat + logs + Telegram are the UI.
