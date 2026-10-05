# KancaHub — Command Cheat-Sheet

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
| `kancahub stack inject` | Inject existing `results.json` keys into 9Router | `kancahub stack inject -i results.json --verify` |
| `kancahub stack validate` | Validate a single `cfut_` token | `kancahub stack validate --token cfut_x --account-id abc123` |
| `kancahub stack sync` | Verify + prune dead 9Router connections | `kancahub stack sync --prune` |
| `kancahub stack manage` | Verify/list CF tokens via `cf_workerai_manager` | `kancahub stack manage --token-file tokens.txt --out-csv out.csv` |
| `kancahub stack web` | Launch the Auto-FreeCF web UI | `kancahub stack web --port 8080 --open` |

Key `signup` flags: `-n N` accounts · `--warp` (clean egress first) · `--proxy URL` · `--proxy-pool FILE` · `--gateway [URL]` · `--headless` · `--fast` · `--workers N` · `--delay S` · `--retry N` · `--no-inject` · `--export-txt FILE` · `--output FILE`.

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
| `kancahub thk create-key` | Create an API key for an existing account | `kancahub thk create-key --label main` |
| `kancahub thk test-key` | Test a `thk_` key | `kancahub thk test-key thk_live_xxx` |
| `kancahub thk enable-free` | Enable free models for an account | `kancahub thk enable-free` |
| `kancahub thk check-proxies` | Scan configured proxies | `kancahub thk check-proxies` |
| `kancahub thk status` | Account free-tier status | `kancahub thk status` |
| `kancahub thk inject` | Inject `thk_` keys into 9Router | `kancahub thk inject -i account.json --verify` |
| `kancahub thk sync` | Verify + prune TokenHarbor connections | `kancahub thk sync --prune` |

## `proxy` — PetaniProxy

| Command | What it does | Example |
|---|---|---|
| `kancahub proxy harvest` | Harvest + validate public proxies | `kancahub proxy harvest --country US --protocol socks5 --target 30` |
| `kancahub proxy fast` | Ultra-fast aiohttp harvester | `kancahub proxy fast --target 30 --max-latency 1200` |
| `kancahub proxy serve` | Rotating gateway + REST API + dashboard on a port | `kancahub proxy serve --port 8888` |
| `kancahub proxy gateway` | Alias for `serve` | `kancahub proxy gateway --port 8888` |
| `kancahub proxy daemon` | 24/7 auto-healing gateway on `:8888` | `kancahub proxy daemon` |
| `kancahub proxy residential` | Webshare residential hunter | `kancahub proxy residential -n 2` |
| `kancahub proxy warp` | Generate Cloudflare WARP profile | `kancahub proxy warp` |
| `kancahub proxy grok` | Farm Grok/xAI accounts | `kancahub proxy grok -n 2 --mail-provider duckmail` |
| `kancahub proxy pipeline` | Async pipeline: Webshare + Grok concurrently | `kancahub proxy pipeline -n 10` |
| `kancahub proxy sync9r` | Harvest and sync straight into 9Router DB | `kancahub proxy sync9r --target 20 --db auto` |
| `kancahub proxy export` | Export harvested proxies | `kancahub proxy export --to-pool` |
| `kancahub proxy test` | Validate a proxy pool | `kancahub proxy test --pool signup_from_scratch/proxies.txt` |
| `kancahub proxy stats` | Live gateway stats | `kancahub proxy stats` |
| `kancahub proxy api` | Call a gateway REST endpoint | `kancahub proxy api /api/all` |

## `grok` — Grok/xAI farm (grok-register)

| Command | What it does | Example |
|---|---|---|
| `kancahub grok run` | Run the registration flow (CLI) | `kancahub grok run -n 1` |
| `kancahub grok web` | Launch the WebUI on `127.0.0.1:8092` | `kancahub grok web` |
| `kancahub grok gui` | Launch the Tk GUI | `kancahub grok gui` |
| `kancahub grok retry` | Retry a pending file | `kancahub grok retry --pending accounts_1.txt.pending.jsonl` |
| `kancahub grok pool` | Show the grok2api token pool | `kancahub grok pool` |

## `k12` — ChatGPT K-12 teacher verification

> Out of scope for the beginner guide; listed here for completeness.

| Command | What it does | Example |
|---|---|---|
| `kancahub k12 auto` | Full auto: signup + OTP + session capture + SheerID verify | `kancahub k12 auto` |
| `kancahub k12 verify` | Verify a SheerID URL | `kancahub k12 verify <sheerid-url> --gateway` |
| `kancahub k12 inject` | Inject captured sessions into 9Router | `kancahub k12 inject --session k12_sessions.json` |
| `kancahub k12 sync` | Verify + prune ChatGPT (codex) connections | `kancahub k12 sync --prune` |
| `kancahub k12 modes` | Show the 12 connection modes | `kancahub k12 modes` |

## `yowes` — teacher document generator (for your own use)

| Command | What it does | Example |
|---|---|---|
| `kancahub yowes list` | List countries + document types | `kancahub yowes list` |
| `kancahub yowes schools` | List schools for a country | `kancahub yowes schools --country us` |
| `kancahub yowes make` | Generate documents | `kancahub yowes make --country us --first John --last Doe --school "Norton Elementary"` |
| `kancahub yowes k12` | US teacher docs via the K-12 bridge | `kancahub yowes k12 --first Jane --last Smith --school "Norton Elementary"` |
| `kancahub yowes gui` | Launch the legacy desktop GUI | `kancahub yowes gui` |
| `kancahub yowes mcp` | Run the yowes MCP server (stdio) | `kancahub yowes mcp` |

---

## Common one-liners

```bash
kancahub doctor
kancahub warp up
kancahub region set us
kancahub stack signup -n 1 --warp
kancahub stack sync --prune
kancahub proxy daemon
kancahub thk batch 3 && kancahub thk inject
```
