# Content plan — TikTok/Shorts + Shopee affiliate (locked 2026-10-14)

## Niches (locked)
- **Home living / dapur** — gadget dapur, organizer, hacks rumah.
- **Tech / aksesoris HP** — unboxing gadget murah, aksesoris.
- Faceless-first. Face allowed ONLY as a **consistent AI-generated avatar**
  (Google Flow/Veo via the AI-Pro account) — never random faces, the avatar must
  be the same person every video or the account looks fake.

## Cadence (gated on key supply)
- Target: **1 konten/hari per akun niche** — ONLY when farmed keys are plentiful.
- Today: keys are scarce (2 usable ZCode accounts), so NO pipeline is built yet.
  The user creates the Shorts/TikTok accounts first; pipeline follows when supply
  recovers. Do NOT build the generator until the user says go.

## Pipeline shape (when unlocked)
1. Pick product: high-commission Shopee ID items in the two niches (home living
   converts best for faceless; tech accessories for unboxing).
2. Script: hook (0–3s) → 3 demo beats → CTA + link. ID language, ≤45s.
3. Produce: stock footage + AI voiceover (faceless) OR Flow avatar segment.
4. Post 1/day/account, track link clicks per video; kill formats <0.5% CTR.

## Gig pivot (same date)
- Gig focus moved to **Superteam Earn + Algora** (agent-doable dev bounties).
  `gig_finder.py` already sources both; daily report surfaces them. No auto-bid
  without the user's explicit go (needs paid Freelancer account for FL bids).

## Automated gigs worker (decided 2026-10-14, runs on LightVela)
- Box roles: **Railway = farm, LightVela = gigs.** LightVela spec: Ubuntu 24.04,
  2 core / 7.4 GB / 50 GB, SG IP 161.120.177.213, idle, sudo nopasswd.
- Workers run **both in parallel** on LightVela (dev-bounty executor + content
  pipeline); burst compute spills to Railway. No Google Workspace support on
  the box yet (no Chrome, no Google login) — OAuth/Gmail flows need user setup.
- Payout: user's wallet. Secret lives ONLY in `~/.config/auto-freecf/.env`
  (600, `wallet_superteam`) — NEVER in bashrc (exports leak to subprocesses),
  never in the repo, never on throwaway boxes.
- Autonomy: **Telegram notify + auto-submit under $X** (threshold TBD by user).
  Above $X = human approves via Telegram. WhatsApp: no API for personal
  accounts — Telegram Bot API is the channel (needs BotFather token from user).
- NOT built yet: worker scripts, notifier, threshold. Build after the user
  supplies the Telegram bot token + confirms $X.
