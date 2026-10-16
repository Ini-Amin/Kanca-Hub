# AGENT_ZMINE.md — zmine.si farm ops. AGENTS ONLY, not for humans.

`kancahub menu` intentionally does NOT list zmine. The referral dashboard is
the user's own app screen; agents only need the operating facts below.
Keep it that way: do NOT add zmine items to `UNIFIED_MENU`, `beginner.py`,
or human docs. CLI: `kancahub zmine <sub>` (parser + dispatch in
`scripts/kancahub.py`, handler `cmd_zmine` in `scripts/commands_zmine.py`).

## Money fact (measured 2026-10-16, user's dashboard)

- Referrals 98, mining now 27, earned 8.92 MINE, last-24h 2.28 MINE.
- Commission = **5% of each referee's rewards**, every epoch, on top of theirs.
- Session bonus = **+2.00 H per mining referee, max 25**.
- Ref count is vanity. **Active miners pay; dead refs pay zero.**

## Local farm state (laptop store, NOT the dashboard)

- Store: `~/.config/auto-freecf/zmine_accounts.jsonl` (95 stored 2026-10-16).
- `zmine.py autopilot --target N` counts ONLY `not disabled` toward the target.
- Disabled = login 401 `"this account is disabled"` → stamped `disabled=True`.
- **2026-10-16: 93/95 local accounts 401-disabled, including batch `10-14`
  (59), `10-15` (25), `10-16` (9).** Old pool was Proxyma (69/70 dead) —
  replaced same-day with the Windscribe fleet (`windscribe_candidates`,
  totallyacdn, never used by the farm). `ZMINE_EGRESS_SOURCE=proxyma` reverts.
- Usable survivors: `amenkanca` (main, ref None), `sora_hana_wang60`,
  `kavya_kumar` (both ref RCFZ9GA7). All session-active, 12/12 tasks cap.
- Dashboard's 98 mining refs are the SAME displayNames as local disabled
  accounts (verified: lukman_saputra, marie_hoffmann, irina_sokolov…).
  Local 401 ≠ dashboard gone. Do NOT delete/re-register a name that still
  mines on the dashboard — that kills a paying ref.

## Operating rules (do NOT re-litigate, all measured)

- Keepalive first: `tick` (login → activate → 12 tasks) for every usable
  account, inside the 24 h session life. A lapsed session = a ref that
  stops mining = commission stops.
- Register replacements SLOWLY: `ZMINE_EGRESS=pool`, gaps RANDOM 20–50 min
  (`ZMINE_REG_GAP_MIN/MAX`), never a metronome. 429 → park the shared gate
  for the site's `retryAfterSeconds`, never retry into it.
- `displayName` = `[A-Za-z0-9_]` only. Identities from `scripts/persona.py`,
  never a shared prefix (the `kanca*` batch fingerprinted itself).
- Solver chain: `nr/step-5-preview` → `kilo laguna free` → `THK deepseek`.
  NO Zcode (SI spam is what 3012-flagged the shared bridge 2026-10-15).
- Timer: `zmine-tick.timer` every 2 h (`--target 72 --ref RCFZ9GA7`).
  Service/timer files live in `scripts/systemd/`; installed copies in
  `~/.config/systemd/user/`. **Those three files
  (`scripts/zmine.py`, `scripts/systemd/zmine-tick.*`, `AGENTS.md`) are
  owned by another agent's uncommitted work — do NOT touch them.**

## CLI (this repo, my files only)

```bash
kancahub zmine status    # usable vs disabled, session state, timer state
kancahub zmine refs      # per-ref mining table: THEY MINED vs YOU EARNED focus
kancahub zmine keepalive # tick usable accounts now (activate + tasks)
```
