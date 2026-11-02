# KancaHub — Command Cheat-Sheet

> **VERIFIED 2026-10-05** — smoke-tested against `scripts/kancahub.py` with the
> managed venv. Every command below parses (`--help` exit 0). No-side-effect
> invocations that were actually run: `doctor`, `warp status`, `region list` /
> `region current` / `region show us`, `proxy stats`, `grok pool`,
> `github check`, `gmail check`, `yowes list`,
> `yowes schools --country us`. Account-creating / mail-sending / server
> commands were only checked at the `--help` level by design.

One line per command, with a runnable example. `kancahub` with no arguments
opens the interactive menu. Add `--help` to any group/command for options.

## Top level

| Command | What it does | Example |
|---|---|---|
| `kancahub` | Interactive beginner menu | `kancahub` |
| `kancahub --help` | Full reference (ASCII banner + all groups) | `kancahub --help` |
| `kancahub doctor` | Health/dependency check across all tools & services | `kancahub doctor` |

## `stack` — Auto-FreeCF (Cloudflare accounts & tokens)

| Command | What it does | Example |
|---|---|---|
| `kancahub stack signup` | Create new Cloudflare accounts + Workers AI tokens (runs the full signup→verify→inject pipeline) | `kancahub stack signup -n 1 --warp` |
| `kancahub stack login` | Log in to an EXISTING account (email:password or Google) | `kancahub stack login you@x.com:pass` |
| `kancahub stack inject` | Inject existing `results.json` keys into 9Router | `kancahub stack inject -i results.json --dry-run` |
| `kancahub stack validate` | Validate a single `cfut_` token | `kancahub stack validate --token cfut_x --account-id abc123` |
| `kancahub stack sync` | Verify + prune dead 9Router connections | `kancahub stack sync --prune` |
| `kancahub stack manage` | Verify/list CF tokens via `cf_workerai_manager` | `kancahub stack manage --token-file tokens.txt --out-csv out.csv` |
| `kancahub stack web` | Launch the Auto-FreeCF web UI | `kancahub stack web --port 8080 --open` |
| `kancahub stack cookie-import` | Import a Cookie-Editor JSON export → extract `account_id` + mint token → `accounts.json` | `kancahub stack cookie-import cookies.json akun-1` |

Key `signup` flags: `-n N` accounts · `--warp` (clean egress first) · `--proxy URL` · `--proxy-pool FILE` · `--gateway [URL]` · `--headless` · `--fast` · `--workers N` · `--delay S` · `--retry N` · `--no-inject` · `--export-txt FILE` · `--output FILE`.

> **Model honesty:** the free Workers AI tier serves small open models
> (`@cf/meta/llama-3.1-8b-instruct` default, `@cf/openai/gpt-oss-120b`,
> `@cf/meta/llama-3.3-70b-instruct-fp8-fast` for probes). Fine for drafts,
> classification, and router fallback — not a replacement for a flagship
> model (GLM-5.3 / GPT / Claude class). Farm it for volume and fallback,
> not for frontier quality.
>
> relay — see [BYOD_MAIL.md](BYOD_MAIL.md) for the 15-minute self-host.

## `warp` — Cloudflare WARP tunnel

| Command | What it does | Example |
|---|---|---|
| `kancahub warp status` | Show tunnel state + egress IP (default) | `kancahub warp status` |
| `kancahub warp up` | Generate profile (if needed) + bring tunnel up | `kancahub warp up` |
| `kancahub warp down` | Bring the tunnel down | `kancahub warp down` |
| `kancahub warp gen` | Generate a fresh WARP profile only | `kancahub warp gen` |

## `region` — promo/bonus region profiles

| Command | What it does | Example |
|---|---|---|
| `kancahub region list` | List regions (`us uk sg id de jp in br au ca any`) | `kancahub region list` |
| `kancahub region current` | Show the active region (default) | `kancahub region current` |
| `kancahub region set` | Set the active region | `kancahub region set us` |
| `kancahub region show` | Show one region's details | `kancahub region show sg` |
| `kancahub region clear` | Reset to auto (nearest) | `kancahub region clear` |

## `thk` — TokenHarbor (free API keys → 9Router)

| Command | What it does | Example |
|---|---|---|
| `kancahub thk setup` | Full interactive setup on TokenHarbor | `kancahub thk setup` |
| `kancahub thk batch` | Create N TokenHarbor accounts | `kancahub thk batch 3` |
| `kancahub thk create-key` | Create an API key for an existing account (account chosen interactively) | `kancahub thk create-key` |
| `kancahub thk test-key` | Test a `thk_` key | `kancahub thk test-key thk_live_xxx` |
| `kancahub thk enable-free` | Enable free models for an account | `kancahub thk enable-free` |
| `kancahub thk check-proxies` | Scan configured proxies | `kancahub thk check-proxies` |
| `kancahub thk status` | Account free-tier status — ⚠️ **currently broken** (see Known issues) | `kancahub thk status --help` |
| `kancahub thk inject` | Inject `thk_` keys into 9Router | `kancahub thk inject -i account.json --verify` |
| `kancahub thk sync` | Verify + prune TokenHarbor connections | `kancahub thk sync --prune` |
| `kancahub thk setup-env` | Wire `harbor` config: `config.toml` + Tempik base_url + capsolver + proxies | `kancahub thk setup-env --status` |

## `proxy` — PetaniProxy

| Command | What it does | Example |
|---|---|---|
| `kancahub proxy start` | **[guided]** 1-question proxy launcher (WARP, Gateway, Residential, Daemon) | `kancahub proxy start` |
| `kancahub proxy verify` | **[live proof]** Prove your IP is masked (real vs gateway egress) | `kancahub proxy verify` |
| `kancahub proxy harvest` | Harvest + validate public proxies (supports `--country`, `--protocol`, `--loop`, `--sync-9router`) | `kancahub proxy harvest --country US --protocol socks5 --target 30 --loop 15` |
| `kancahub proxy fast` | Ultra-fast aiohttp harvester | `kancahub proxy fast --target 30 --max-latency 1200` |
| `kancahub proxy serve` | Rotating gateway + REST API + dashboard on a port | `kancahub proxy serve --port 8888` |
| `kancahub proxy gateway` | Alias for `serve` | `kancahub proxy gateway --port 8888` |
| `kancahub proxy daemon` | 24/7 auto-healing resilient gateway on `:8888` (auto-refill + health-check) | `kancahub proxy daemon` |
| `kancahub proxy residential` | Webshare residential hunter | `kancahub proxy residential -n 2` |
| `kancahub proxy warp` | Generate Cloudflare WARP profile | `kancahub proxy warp` |
| `kancahub proxy grok` | Farm Grok/xAI accounts | `kancahub proxy grok -n 2 --mail-provider duckmail` |
| `kancahub proxy pipeline` | Async pipeline: Webshare + Grok concurrently | `kancahub proxy pipeline -n 10` |
| `kancahub proxy sync9r` | Harvest and sync straight into 9Router DB | `kancahub proxy sync9r --target 20 --db auto` |
| `kancahub proxy export` | Export harvested proxies | `kancahub proxy export --to-pool` |
| `kancahub proxy test` | Validate a proxy pool | `kancahub proxy test --pool signup_from_scratch/proxies.txt` |
| `kancahub proxy stats` | Live gateway stats | `kancahub proxy stats` |
| `kancahub proxy api` | Call a gateway REST endpoint | `kancahub proxy api /api/all` |
| `kancahub proxy res-gateway` | Bridge gateway for residential/authenticated proxies (`--pool` required) | `kancahub proxy res-gateway --pool res.txt --port 8899` |
| `kancahub proxy nharvest` | **[native]** harvest + validate public proxies (no PetaniProxy) | `kancahub proxy nharvest --target 20 --out-txt live.txt` |
| `kancahub proxy nhealth` | **[native]** check a pool file | `kancahub proxy nhealth --pool live.txt` |
| `kancahub proxy ngateway` | **[native]** rotating gateway for a pool | `kancahub proxy ngateway --pool live.txt --port 8899` |

> `proxy` has two backends: the historical PetaniProxy passthrough (`harvest`,
> `fast`, `serve`, `daemon`, `residential`, `pipeline`, …) and the self-contained
> native toolkit in `scripts/proxy_lib.py` (`nharvest`, `nhealth`, `ngateway`).
> `kancahub doctor` reports which backend is active.

## `grok` — Grok/xAI farm (grok-register)

| Command | What it does | Example |
|---|---|---|
| `kancahub grok run` | Run the registration flow (CLI) | `kancahub grok run -n 1` |
| `kancahub grok web` | Launch the WebUI on `127.0.0.1:8092` | `kancahub grok web` |
| `kancahub grok gui` | Launch the Tk GUI | `kancahub grok gui` |
| `kancahub grok retry` | Retry a pending file | `kancahub grok retry --pending accounts_1.txt.pending.jsonl` |
| `kancahub grok pool` | Show the grok2api token pool | `kancahub grok pool` |
| `kancahub grok inject` | Inject Grok SSO tokens into 9Router via a grok2api bridge | `kancahub grok inject --base-url http://127.0.0.1:8787/v1 --dry-run` |

## `github` — GitHub Education signup helper

> Helper flow only. Arkose/CAPTCHA puzzles, the student-ID photo / identity
> attestation, MFA and GitHub's manual review are **not** automated — finish
> those by hand. Saves to `~/Auto-FreeCF/github_accounts.json`.

| Command | What it does | Example |
|---|---|---|
| `kancahub github farm` | Sign up a GitHub account + start the Education application (school mailbox + M365 OTP) | `kancahub github farm --index 1 --dry-run` |
| `kancahub github check` | Check deps + school mailbox config, then exit | `kancahub github check` |

Config: `~/.config/auto-freecf/.env` (`SCHOOL_EMAIL`, `SCHOOL_MAIL_PASSWORD`, `SCHOOL_MAIL_URL`).

## `mail` — school Outlook inbox reader (M365/nodriver)

> M365 blocks IMAP basic auth, so the OTP is scraped from the web UI. The
> session lives in `~/.config/auto-freecf/school-profile` and survives runs.

| Command | What it does | Example |
|---|---|---|
| `kancahub mail test` | Log in and list recent inbox subjects (selftest) | `kancahub mail test` |
| `kancahub mail otp` | Wait for an OpenAI/ChatGPT verification code | `kancahub mail otp --timeout 300` |
| `kancahub mail login` | Log in and leave the browser open for inspection | `kancahub mail login` |

## `gmail` — Gmail account farm (nodriver + Chrome)

| Command | What it does | Example |
|---|---|---|
| `kancahub gmail farm` | Create N Gmail accounts, append results to the JSON out file | `kancahub gmail farm --count 2 --proxy http://127.0.0.1:8888` |
| `kancahub gmail dry-run` | Walk the flow without submitting | `kancahub gmail dry-run` |
| `kancahub gmail check` | Report dependencies and exit | `kancahub gmail check` |

> Headless cannot complete phone verification — expect `pending_verification` /
> `failed` rows unless you run headed and finish by hand.

## `yowes` — teacher document generator (for your own use)

| Command | What it does | Example |
|---|---|---|
| `kancahub yowes list` | List countries + document types | `kancahub yowes list` |
| `kancahub yowes schools` | List schools for a country | `kancahub yowes schools --country us` |
| `kancahub yowes make` | Generate documents | `kancahub yowes make --country us --first John --last Doe --school "Norton Elementary"` |
| `kancahub yowes gui` | Launch the legacy desktop GUI | `kancahub yowes gui` |
| `kancahub yowes mcp` | Run the yowes MCP server (stdio) | `kancahub yowes mcp` |

## `autoclaw` — AutoClaw 2.0.3 (Zhipu zwork fission bonuses & z.ai OAuth)

| Command | What it does | Example |
|---|---|---|
| `kancahub autoclaw` | Interactive menu (claim / status / wallets / accounts / bind / captcha) | `kancahub autoclaw` |
| `kancahub autoclaw claim` | 1-click z.ai OAuth claim, prompts for invite code if missing (Enter = login only) | `kancahub autoclaw claim` |
| `kancahub autoclaw status` | Check fission campaign status & reward amounts (default) | `kancahub autoclaw status` |
| `kancahub autoclaw captcha` | Show Aliyun Captcha 2.0 config (scene_id, prefix, region) | `kancahub autoclaw captcha` |
| `kancahub autoclaw oauth-url` | Generate z.ai OAuth URL using Aliyun captcha verify proof | `kancahub autoclaw oauth-url --verify-param <proof>` |
| `kancahub autoclaw login` | Complete z.ai OAuth login (via `--listen`, `--verify-param`, or `--code`/`--state`) | `kancahub autoclaw login --listen --bind INV123` |
| `kancahub autoclaw bind` | Bind inviter referral code, prompts if omitted (+800 inviter tokens) | `kancahub autoclaw bind INV123` |
| `kancahub autoclaw wallets` | Check token balances and grant instances | `kancahub autoclaw wallets` |
| `kancahub autoclaw accounts` | List local saved accounts in `autoclaw_accounts.jsonl` | `kancahub autoclaw accounts` |

## `freebuff` — Freebuff coding agent (sticky IP & fingerprint slots)

| Command | What it does | Example |
|---|---|---|
| `kancahub freebuff status` | Show all slots, active slot, fingerprint ID, proxy, and auth status | `kancahub freebuff status` |
| `kancahub freebuff login` | Run browser login in active slot with its locked fingerprint and sticky proxy | `kancahub freebuff login` |
| `kancahub freebuff run -- <args>` | Execute freebuff CLI in the isolated slot environment | `kancahub freebuff run -- -v` |
| `kancahub freebuff rotate` | Retire active slot, create fresh slot with new fingerprint & optional proxy | `kancahub freebuff rotate --proxy http://127.0.0.1:25346` |
| `kancahub freebuff switch <slot>` | Switch active slot | `kancahub freebuff switch slot_1` |
| `kancahub freebuff set-proxy <url>` | Bind or update proxy for a slot | `kancahub freebuff set-proxy http://127.0.0.1:25344` |

## `supplies` — Community AI supplies & harvester fleet

| Command | What it does | Example |
|---|---|---|
| `kancahub supplies list` | List all supplies with target models and features | `kancahub supplies list` |
| `kancahub supplies info <name>` | Show detailed architecture, auth flow, and endpoints of a supply | `kancahub supplies info orvispool` |
| `kancahub supplies clone <name>` | Clone a supply into `~/Auto-FreeCF-vendor-refs` | `kancahub supplies clone orvispool` |
| `kancahub supplies farm [name]` | Run per-provider automated farm or interactive selection | `kancahub supplies farm orvispool` |

## `orvis` — Orvis AI frontier models (100 free credits/acct)

| Command | What it does | Example |
|---|---|---|
| `kancahub orvis accounts` | List harvested Orvis accounts and credit balance | `kancahub orvis accounts` |
| `kancahub orvis create` | Bulk-create new accounts headlessly (AWS Cognito OTP) | `kancahub orvis create --count 1` |
| `kancahub orvis serve` | Start OpenAI-compatible bridge on port 8796 | `kancahub orvis serve --port 8796` |
| `kancahub orvis connect` | Inject Orvis node straight into 9Router SQLite DB | `kancahub orvis connect` |
| `kancahub orvis test` | Smoke-test model completions via local bridge | `kancahub orvis test --model anthropic/claude-sonnet-4-20250514` |

## `komafree` — Koma-Free keyless instant vLLM inference

| Command | What it does | Example |
|---|---|---|
| `kancahub komafree create` | Mint N keyless accounts locally in microseconds | `kancahub komafree create -n 5` |
| `kancahub komafree accounts` | Show stored accounts table | `kancahub komafree accounts` |
| `kancahub komafree chat` | Send prompt upstream (`koma/apple`) | `kancahub komafree chat -p "ping"` |
| `kancahub komafree doctor` | Connectivity and latency diagnosis | `kancahub komafree doctor` |

## `stepfun` — StepFun Studio reasoning models (Step-5 Preview)

| Command | What it does | Example |
|---|---|---|
| `kancahub stepfun models` | List StepFun flagship reasoning models | `kancahub stepfun models` |
| `kancahub stepfun doctor` | Check config, token quota and 9Router DB status | `kancahub stepfun doctor` |
| `kancahub stepfun accounts` | List stored StepFun accounts | `kancahub stepfun accounts` |
| `kancahub stepfun connect` | Wire StepFun Studio into 9Router SQLite DB | `kancahub stepfun connect` |

## `opensatan` — OpenSatan anonymous uncensored pool

| Command | What it does | Example |
|---|---|---|
| `kancahub opensatan create` | Mint anonymous identities without browser in 4s | `kancahub opensatan create --count 1` |
| `kancahub opensatan list` | List active anonymous identities | `kancahub opensatan list` |
| `kancahub opensatan chat` | Chat with unaligned OpenSatan models | `kancahub opensatan chat --prompt "hello"` |

## `oxalpha` — Ox Alpha GLM-5.3 Flash 1M token reasoning

| Command | What it does | Example |
|---|---|---|
| `kancahub oxalpha test` | One-off chat test with GLM-5.3 Flash reasoning | `kancahub oxalpha test "explain gravity"` |
| `kancahub oxalpha create` | Harvest sessions into pool | `kancahub oxalpha create --count 1` |
| `kancahub oxalpha list` | Show active pool sessions | `kancahub oxalpha list` |
## Common one-liners

```bash
kancahub doctor
kancahub warp up
kancahub region set us
kancahub stack signup -n 1 --warp
kancahub stack sync --prune
kancahub proxy daemon
kancahub thk batch 3 && kancahub thk inject --dry-run
```

---

## Known issues (reported, not fixed)

- `kancahub thk status` exits with an argparse error: the wrapper calls
  `tools.tokenharbor.cli status` without credentials, but that CLI requires
  `--email` and `--password`. The `--help` still works; the command itself does
  not. Fix belongs in `scripts/kancahub.py` / `harbor` CLI, not in these docs.
- `kancahub gmail dry-run --help` shows `--count` / `--proxy` / `--out` with no
  help text (cosmetic).
