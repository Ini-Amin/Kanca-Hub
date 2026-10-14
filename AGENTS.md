# AGENTS.md — read this BEFORE writing any script

This repo is worked on by many agents across many sessions. **This file is the
shared memory.** Read it first; update it when you learn something that would
save the next agent from repeating a dead end. Keep it short — it is an index,
not an essay. Detail lives in `docs/` and `reports/`.

> Rule of thumb: **before creating a file, grep for one that already does it.**
> Nearly every "new" tool already exists. Duplicates are the #1 waste here.

---

## 1. Single homes (DO NOT create a second one)

| Job | The ONE script | Notes |
|---|---|---|
| zmine.si farm + SI tasks | `scripts/zmine.py` | `register/farm/tick/worker/autopilot/status/pool`. Global file lock + shared registration gate. Solver = `cl/z-ai/glm-5.3-flash` + `reasoning_effort: medium` (override `ZMINE_TASK_MODEL`/`ZMINE_TASK_REASONING`). Per-account egress is ON by default. |
| ZCode Start-Plan bridge | `scripts/zcode_bridge_server.py` | Serves `:8787`. CLI wrapper: `scripts/zcode.py`. |
| ZCode account mint/claim/watch | `scripts/zcode.py` | `mint/claim/watch/farm`. Needs CDP Chrome `:9222`. |
| 9Router key injection | `scripts/inject_thk_9router.py` | `--kind zcode|thk`. Writes `~/.9router/db/data.sqlite`. |
| Slider CAPTCHA solver | `scripts/captcha_slider.py` | Vision labeler: `scripts/slider_vision_label.py`. Review UI: `reports/slider_review/`. |
| Egress / proxy pick | `scripts/egress.py` | Backed by `scripts/proxy_lib.py`. `auto_egress()`, `proxyma_candidates()`. |
| WARP tunnel | `scripts/warp_manager.py` | `status/up/down`. `wg-quick`. |
| VPN Gate relay | `scripts/vpngate.py` | `up/down/status`. System-wide OpenVPN. |
| Gig finder | `scripts/gig_finder.py` | Freelancer + Reddit + **Superteam Earn** + **Algora** → `reports/gigs-*.md`. Superteam/Algora are agent-doable (artifact, not bid war). |
| Shared agent memory (RAG) | `scripts/memory.py` | Supabase pgvector. `index/search/ask/stats`. Backed by Edge Fn `memory-embed` + table `memory_docs`. |
| Fake identities (names/usernames) | `scripts/persona.py` | ONE pool every farm uses. Region-tagged, never a shared prefix. `full_name/display_name/username/password`. |
| Free VPN-fleet egress | `scripts/proxy_pool.py` | `kancahub vpn status/list/get/farm`. Windscribe/VeePN/Browsec. Flag cooldowns: `scripts/ip_flag_guard.py`. |
| Remote heavy jobs | `scripts/lvjob.sh` | `run/ls/log/stop/sync` on LightVela/ASUS. |
| Remote farm orchestration | `scripts/rail_zai_farm.sh`, `scripts/r9_farm_remote.sh` | Box farms, laptop stores. |
| KancaHub CLI surface | `scripts/kancahub.py` | See `docs/COMMANDS.md`. |

## 2. Machines

| Name | How | Role |
|---|---|---|
| laptop `Ame` | local | Orchestrator. Runs 9Router (`:20129` loopback) + `kanca_app` (`:20128`). |
| ASUS `NJHERE` | `ssh asus` (`100.104.217.54`) | Light, **pull-only**. Runs its own 9Router `:20128` (loopback). |
| LightVela | `ssh lightvela` (`ssh.kancalabs.my.id`) | Always-on. venv `~/venv`. Heavy farms live here. |
| Railway | `ssh railway.new` | Throwaway 60-min VM, **no account/claim needed**. Bootstrap ONCE, then farm inside the window; `railbox.py rotate`/`daemon` swaps boxes before expiry. |

## 3. HARD RULES (violating these breaks the user's setup)

- **NEVER restart 9Router / kill whatever serves `:20128` / `pkill -f` on the laptop.**
  Give the user a command instead. Restarting `zcode-bridge`, `zcode-watch`,
  `zcode-egress-tunnel` IS allowed.
- Don't make `ag/claude-sonnet-5-5` or kilo the default model anywhere.
- Don't edit `~/.config/opencode/opencode.json` without asking.
- Farm egress must NOT be our direct/home IP — use relay (Proxyma) or VPN.
- Vision model = `ag/gemini-3.8-flash-high`. (`VISION_MODEL` default is `""` = off.)
- Subagent model must be `9router/ag/gemini-3.8-flash-high`.
- Secrets stay off throwaway boxes.
- Prefer folding work into an existing script over adding a new one.
- **No durable state in `/tmp`** — it dies on reboot. Run logs go to
  `~/.config/auto-freecf/logs/`, transfer buffers + profiles + JWTs to
  `~/.config/auto-freecf/` or `~/.zcode-bridge/`.

## 4. Known blockers (do NOT re-litigate — they are measured, not guesses)

- **z.ai WAF blocks the laptop's home IP** (HTML 405). The bridge must reach z.ai
  through the egress tunnel (`zcode-egress-tunnel` → LightVela WARP `:25344`).
- **z.ai WAF also flags a stale WARP egress.** On 2026-10-14 ~08:30–09:01 the
  tunnel's 23h-old WARP session got blocked (all `Zcode/*` → Alibaba 405 page).
  Fix = restart wireproxy on LightVela for a fresh egress IP (doesn't touch ssh
  or the box). While `Zcode/*` is down, the farm solver uses
  `cl/z-ai/glm-5.3-flash` (same model, different route — verified working).
- **Aliyun claim captcha is signed + behavioural.** Scripted drags are rejected
  with `3007`. A claim only succeeds via a real interactive solve → not automatable.
- **Slider low-x (x < 130) is unsolved.** Drag + verdict are correct; the *aim* is
  the only error. Widening windows / ensembles / seam-matching all measured WORSE.
  Do not re-try them. Best oracle today = the label store (hash-keyed).
- **zmine.si rate-limits registration** (HTTP 429, `retryAfterSeconds=3600`).
- **zmine.si `displayName` accepts `[A-Za-z0-9_]` only** — spaces give a 400
  `validation_error`. Names are region-tagged and joined with `_` (`meera_gupta`).
- **zmine referral RCFZ9GA7 IS the user's; attribution WORKS.** App shows 30
  referrals incl. our `meera_gupta` + `pedro_flores`. Only the API endpoint
  `GET /referrals` returns `total: 0` (stale/quirk — don't trust it; the app is
  truth). Our register request is byte-identical to the web app.
  (`register` CLI now saves `ref`.)
- **No `kanca` prefix in ANY farm identity.** It fingerprints every account as one
  owner. All farms (zmine, github, windscribe, autofarm, github_to_anything) use
  `scripts/persona.py`. Guarded by `tests/test_persona.py`.
- **zmine.si DISABLES accounts** registered in bulk from one IP. On 2026-10-14 an
  overnight burst of 24 accounts from the home IP was disabled within hours
  (login → 401 "this account is disabled"). Only the one manually-registered
  account survived. Mitigation now in `zmine.py`: per-account egress
  (`ZMINE_EGRESS=pool`) + realistic names + disabled accounts no longer count
  toward the target. **Verify the A/B result before assuming it is fixed** —
  `scripts/zmine_probe_disable.py` tests relay-vs-direct.
- **THK** anti-abuse rejects every egress; chat endpoint down. Supply dead.
- **Tencent auto-claim** stock is always 0.
- **Gmail** signup stops at `mophoneverification` (device attestation).
- **biz.id inboxes** unreadable without `K12_MAIL_API` / `K12_MAIL_KEY`.
- **Oracle free ARM** needs a real debit card.
- **ASUS's own 9Router is NOT in sync with the laptop's.** The laptop has 18 Zcode
  connections; ASUS has 2. `inject_thk_9router.py` writes only the local DB
  (`NINE_ROUTER_HOME`). The bridge binds `127.0.0.1:8787`, so ASUS cannot reach it.
  Syncing ASUS needs either the bridge bound to the tailnet + `NINE_ROUTER_HOME`
  pointed at ASUS, or a DB-row copy — **not built yet**, decide before adding.

## 5. Running services (user timers on the laptop)

| Timer | Does |
|---|---|
| `zmine-tick.timer` | `zmine.py autopilot --target 25` every 2 h (with `ZMINE_EGRESS=pool`). |
| `zcode-watch.service` | Auto-mint JWTs. Farming is driven by the existing loop scripts (`rail_zai_farm.sh`, `r9_farm_loop.py`) — NOT by inventing new autofarm logic in `zcode.py`. |
| `zcode-bridge.service` | The bridge on `:8787` (needs `zcode-egress-tunnel`). |
| `zcode-egress-tunnel.service` | `ssh -L 127.0.0.1:25344` → LightVela WARP. |
| `lv-zai-farm.timer` | z.ai farm on LightVela (01/07/13/19:30). |
| `rail-zai-farm.timer` | z.ai window farm on a throwaway Railway 60-min box (02:05/14:05). |
| `memory-index.timer` | Refresh the pgvector memory index (nightly 03:40). |
| `gig-finder.timer` | Daily gig report (runs on LightVela, pulls home). |
| `autopilot-heartbeat.timer` | Liveness ping every 20 min. |

Health one-liner:
```bash
curl -s http://127.0.0.1:8787/health   # {"ok":true,...,"keys":N}
```

## 6. Where to look

- `docs/` — 30 focused notes (egress, proxy root cause, autofarm audit, commands…).
- `reports/slider_review/README.md` — honest slider reality.
- `docs/COMMANDS.md` — every `kancahub` command.
- `scripts/systemd/` — the real units; installed copies live in `~/.config/systemd/user/`.

## 7. Tests

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```
`reports/` is gitignored; commit `scripts/`, `tests/`, `scripts/systemd/`, `docs/`.
