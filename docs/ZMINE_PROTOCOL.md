# ZMINE protocol (from https://zmine.si/docs, captured 2026-10-14)

> Source of truth is the live docs page. This file is a working copy for the
> farm (`scripts/zmine.py`). Cells marked `[?]` were numbers the Next.js HTML
> extraction dropped — do NOT treat them as facts, re-check the page.

## 01 · Overview — numbers that never change

- Total supply: **210.00M MINE** (fixed; leaves the pool only via epoch rewards).
- Epoch: **10 minutes**. First month: **24,305.5555 MINE per epoch**.
- Halving: **every 4,320 epochs = 30 days**.
- First month releases half the supply; first three months 7/8; first year 99.98%.
  Mining is a one-year event in every way that matters.
- Stake rate: **100.00 H per ZEC**.
- Free pool: **1.00 kH shared, max 25% of an epoch**.
- Genesis passes: **3,000 ever, 30.00 H each, permanent**.
- Referral commission: **5%** (paid on top, from the pool).
- One web service, one worker, one ledger, one custody wallet. App never holds
  keys in the browser.

## 02 · Account

- **Login key + 24-word recovery phrase. No email, no password.** Only one-way
  hashes of both are stored. Login key signs in; phrase reissues a lost key and
  signs out every device. Lose both = lose the account, by design.
- Each account gets a shielded (unified) + transparent Zcash deposit address.
- 2FA (authenticator): required for withdrawals, address changes, key rotation,
  auto-pilot spending once enabled.
- Devices: settings lists every signed-in device (first-seen time); revoke any.
- **`displayName` accepts letters, numbers and underscore only** (measured: spaces
  give a 400 `validation_error`).

## 03 · Protocol — epochs, sessions, free pool

- Epoch ends → worker snapshots every account's hashpower at that instant, pays
  the epoch reward pro rata. One DB transaction per epoch, exactly once.
- Free session: one tap starts a **24-hour session** from the shared **1.00 kH**
  pool, divided among live sessions: **cap 25.00 H, floor 1.00 H** per session.
  Fewer miners = bigger share.
- **Streak: renew inside the last 30 minutes** to keep it; streak multiplies
  session-gated hashpower **+2% per day, up to +20%**.
- Free-share cap: session + referral + task hashpower together take **at most 25%
  of any epoch**. Whatever they would earn above that goes to ZEC-backed
  hashpower pro rata; with 1,000 free accounts + one staked ZEC, the staker earns
  75% of every epoch.
- The Mine page counter is a *projection*; it becomes real only at settlement.
- **Idle accounts earn nothing** (no session/stake/pack/task credit = not in snapshot).

## 04 · Hashpower — six sources + streak

| Source | Rate |
|---|---|
| Free session | 1.00 kH / active sessions, 1–25 H |
| Staked ZEC | 100.00 H per ZEC, around the clock |
| Referrals | **2.00 H per active referral, max 25** |
| Task credits | **5.00 H per correct answer**, 24 h, **12 a day** (max 60 H) |
| Genesis Pass | 30.00 H per pass, permanent, 3,000 ever |
| Streak | +2% per day, max +20% |

- Genesis Pass: numbered mint on the Mint page for `[?]` ZEC; ZEC goes to
  treasury; any number per account; never clipped by the free-share cap;
  non-transferable, non-refundable today.
- Boost packs (ZEC spent → treasury, time-limited hashpower, stacks, no lock):

| Pack | Hashpower | Lasts | Price |
|---|---|---|---|
| Spark | 8.00 H | 24 h | 0.001 ZEC |
| Pulse | 50.00 H | 2 days | 0.005 ZEC |
| Surge | 300.00 H | 5 days | 0.1 ZEC |
| Forge | [?] | 10 days | 0.5 ZEC |
| Titan | 1.60 kH | 15 days | 1 ZEC |
| Apex | 7.00 kH | 30 days | 7 ZEC |

- Holding ZEC you don't need soon → staking is cheaper than packs.

## 05 · Tokenomics / fees

- Halving schedule (per-epoch / era total / cumulative): Oct 2026 24,305.5555 /
  105.00M / 105.00M → Nov 12,152.7777 / 52.50M / 157.50M → Dec 6,076.3888 /
  26.25M / 183.75M → Jan 2027 3,038.1944 / 13.12M / 196.87M → Feb 1,519.0972 /
  6.56M / 203.44M → Mar 759.5486 / 3.28M / 206.72M.
- Staked ZEC stays yours (public staking reserve); unstake = 24 h cooldown.
- Trading fees: taker 0.2%, maker 0 → fee account. Withdrawal: flat fee → fee
  account (network fee carried by treasury). Bridge: provider rate, ZMINE adds
  nothing.
- Market can open days after mining starts. Before the first trade the app shows
  a labelled reference price only.

## 06 · Money

- Deposits: address → balance. Withdrawals: gates (2FA when enabled, operator
  review above a threshold, pause switch, on-chain progress shown, full audit).
- Staking marks ZEC in the ledger; earns at once; unstake = 24 h cooldown.
- Bridge tab: BTC/ETH/SOL/USDC/USDT+ → ZEC deposit address and back.

## 07 · Market

- Central limit order book inside the same ledger. Limit only, price-time
  priority, atomic fills. Prices in ZEC/MINE, 8 decimals, min order 1 MINE.
- Taker 0.2% of what you receive. Self-trade prevented. Candles 5m/15m/1h/4h/1d.
- SI advisor prices pack returns in ZEC from the last traded price; no price → says so.

## 08 · SI (the task layer the farm answers)

- **Earn with tasks: short verification questions, one defensible answer. Correct
  = +5.00 H for `[?]` h, up to 12 a day. Answers faster than 1.5 s do NOT count;
  `[?]` wrong in a row pauses you for `[?]` minutes.** (Our measured behaviour:
  <1.5 s doesn't count; 3 wrong in a row = ~10 min cooldown. Never gamble.)
- X tasks: link account by posting a short code; follow/repost/quote each pay
  10.00 H for `[?]` days, once per account. Account must be `[?]` days old with
  10 followers; one X account ↔ one miner forever.
- Auto-pilot: daily ZEC limit; renews session, buys packs above a return floor,
  places scheduled limit buys. Spending needs 2FA. All logged.

## 09 · Trust / security (farm-relevant)

- Accounts: login key 150 bits (hashed), recovery phrase 24 words (hashed),
  sessions server-side HttpOnly Secure cookie with per-device revocation.
- **Rate limits: per account AND per IP, shared across instances.** This is why
  every account gets its own egress exit.
- Transport: HTTPS+HSTS, per-request CSP nonce, origin check on every mutation,
  no framing, no MIME sniff, strict referrer.
- Wallet is hot (float bounded, rest swept to cold). MINE is a ledger token
  until it issues as a Zcash Shielded Asset after NU7 → ledger balances redeem
  1:1 into shielded notes.
- Rewards depend on participation, never guaranteed.

## Farm implications (what we do with this)

1. Renew sessions inside the last 30 min → streaks compound (+2%/day to +20%).
2. 12 tasks/day/account = up to 60 H free hashpower per account — the SI solver
   is the highest-ROI free action.
3. Referrals pay 2 H/active referral (max 25) + 5% commission → the farm's own
   referral tree (`ref=RCFZ9GA7`) compounds.
4. Free-share cap (25%/epoch) means beyond ~1,000 free sessions the marginal
   account dilutes the pool — watch `reports/` and stop growing when per-account
   payout flattens.
5. Month-1 carries half of everything → farm hardest NOW, not later.
