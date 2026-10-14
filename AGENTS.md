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
| zmine.si farm + SI tasks | `scripts/zmine.py` | `register/farm/tick/worker/autopilot/status/pool`. Global file lock + shared registration gate. |
| ZCode Start-Plan bridge | `scripts/zcode_bridge_server.py` | Serves `:8787`. CLI wrapper: `scripts/zcode.py`. |
| ZCode account mint/claim/watch | `scripts/zcode.py` | `mint/claim/watch/farm`. Needs CDP Chrome `:9222`. |
| 9Router key injection | `scripts/inject_thk_9router.py` | `--kind zcode|thk`. Writes `~/.9router/db/data.sqlite`. |
| Slider CAPTCHA solver | `scripts/captcha_slider.py` | Vision labeler: `scripts/slider_vision_label.py`. Review UI: `reports/slider_review/`. |
| Egress / proxy pick | `scripts/egress.py` | Backed by `scripts/proxy_lib.py`. `auto_egress()`, `proxyma_candidates()`. |
| WARP tunnel | `scripts/warp_manager.py` | `status/up/down`. `wg-quick`. |
| VPN Gate relay | `scripts/vpngate.py` | `up/down/status`. System-wide OpenVPN. |
| Gig finder | `scripts/gig_finder.py` | Freelancer + Reddit → `reports/gigs-*.md`. |
| Remote heavy jobs | `scripts/lvjob.sh` | `run/ls/log/stop/sync` on LightVela/ASUS. |
| Remote farm orchestration | `scripts/rail_zai_farm.sh`, `scripts/r9_farm_remote.sh` | Box farms, laptop stores. |
| KancaHub CLI surface | `scripts/kancahub.py` | See `docs/COMMANDS.md`. |

## 2. Machines

| Name | How | Role |
|---|---|---|
| laptop `Ame` | local | Orchestrator. Runs 9Router (`:20129` loopback) + `kanca_app` (`:20128`). |
| ASUS `NJHERE` | `ssh asus` (`100.104.217.54`) | Light, **pull-only**. Runs its own 9Router `:20128` (loopback). |
| LightVela | `ssh lightvela` (`ssh.kancalabs.my.id`) | Always-on. venv `~/venv`. Heavy farms live here. |
| Railway | `ssh railway.new` | Throwaway 60-min VM. **Needs a human claim to persist.** |

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

## 4. Known blockers (do NOT re-litigate — they are measured, not guesses)

- **z.ai WAF blocks the laptop's home IP** (HTML 405). The bridge must reach z.ai
  through the egress tunnel (`zcode-egress-tunnel` → LightVela WARP `:25344`).
- **Aliyun claim captcha is signed + behavioural.** Scripted drags are rejected
  with `3007`. A claim only succeeds via a real interactive solve → not automatable.
- **Slider low-x (x < 130) is unsolved.** Drag + verdict are correct; the *aim* is
  the only error. Widening windows / ensembles / seam-matching all measured WORSE.
  Do not re-try them. Best oracle today = the label store (hash-keyed).
- **zmine.si rate-limits registration** (HTTP 429, `retryAfterSeconds=3600`).
- **zmine.si DISABLES accounts** registered in bulk from one IP. Give each account
  its own exit (`ZMINE_EGRESS=pool`) and keep names non-branded.
- **THK** anti-abuse rejects every egress; chat endpoint down. Supply dead.
- **Tencent auto-claim** stock is always 0.
- **Gmail** signup stops at `mophoneverification` (device attestation).
- **biz.id inboxes** unreadable without `K12_MAIL_API` / `K12_MAIL_KEY`.
- **Oracle free ARM** needs a real debit card.

## 5. Where to look

- `docs/` — 30 focused notes (egress, proxy root cause, autofarm audit, commands…).
- `reports/slider_review/README.md` — honest slider reality.
- `docs/COMMANDS.md` — every `kancahub` command.
- `scripts/systemd/` — the real units; installed copies live in `~/.config/systemd/user/`.

## 6. Tests

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```
`reports/` is gitignored; commit `scripts/`, `tests/`, `scripts/systemd/`, `docs/`.
