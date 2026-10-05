# KancaHub — Command Cheat-Sheet

> **VERIFIED 2026-10-05** — smoke-tested against `scripts/kancahub.py` with the
> managed venv. Every command below parses (`--help` exit 0). No-side-effect
> invocations that were actually run: `doctor`, `warp status`, `region list` /
> `region current` / `region show us`, `proxy stats`, `grok pool`,
> `github check`, `gmail check`, `k12 modes`, `yowes list`,
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

### Grok SSO → 9Router (`grok2api_bridge`)

9Router's built-in `xai` provider is OAuth-only, so grok.com **SSO cookie**
tokens need a "grok2api" OpenAI-compatible endpoint. `scripts/grok2api_bridge.py`
provides one locally. The upstream is **grounded** (probed 2026-10-05):

- SSO cookie auth → `POST https://grok.com/rest/app-chat/conversations/new` with
  cookies `sso=<t>; sso-rw=<t>` (what `registration_browser.py` / `sso_risk.py`
  use). This is the bridge's **default** (`--auth-mode cookie`). It translates the
  OpenAI body to Grok's `{message, modelName}` and folds the reply back into an
  OpenAI `chat.completion`; `--raw` skips translation.
- OAuth Bearer auth → `https://cli-chat-proxy.grok.com/v1` (what the CPA export
  mints). Anonymous probe answers `401 no auth context`; use
  `--upstream-base https://cli-chat-proxy.grok.com --upstream-path /v1/chat/completions --auth-mode bearer`.

grok.com is behind Cloudflare, so the bridge uses curl_cffi Chrome TLS
impersonation by default (`--no-impersonate` forces httpx).

```bash
# start the bridge (managed venv)
/home/amen/.local/share/auto-freecf/venv/bin/python scripts/grok2api_bridge.py --port 8787
# point 9Router at it
kancahub grok inject --base-url http://127.0.0.1:8787/v1
# or set the env var used by grok_9router.py
export GROK2API_BASE=http://127.0.0.1:8787
```

Flags: `--host` `--port` (default `127.0.0.1:8787`), `--tokens FILE`,
`--upstream-base`, `--upstream-path`, `--auth-mode cookie|bearer`,
`--cf-clearance`, `--no-impersonate`, `--raw`, `--model-map find=replace`.
Live-probed: `/healthz` and `/v1/models` return 200; a dummy/unauthenticated
`/v1/chat/completions` returns grok.com's real `401 Bad credentials` (proving the
route + cookie auth, awaiting a valid SSO).

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

## `k12` — ChatGPT K-12 teacher verification

> Out of scope for the beginner guide; listed here for completeness.
> ⚠️ **The SheerID URL in the K-12 tool's own README is a PLACEHOLDER**
> (`.../verify/xxxabc123?verificationId=xxx123abc`). A fake `verificationId`
> returns SheerID `404 noVerification` — that is expected. Get the real URL from
> **`chatgpt.com/k12-verification`** (click *Verify status* → the SheerID link).

| Command | What it does | Example |
|---|---|---|
| `kancahub k12 run` | Guided prompt: paste URL + pick a connection mode; mirrors the original `run_cmd.bat` menu | `kancahub k12 run` |
| `kancahub k12 verify <url>` | Run the original `script.py` (K12Verifier): SheerID submission + **auto-pass** | `kancahub k12 verify "$URL" --gateway` |
| `kancahub k12 auto` | Original `auto_k12_flow.py` (DrissionPage + temp.tf): ChatGPT signup → OTP → session capture → SheerID, auto-pass | `kancahub k12 auto` |
| `kancahub k12 inject` | Inject captured sessions into 9Router (codex) | `kancahub k12 inject --session k12_sessions.json` |
| `kancahub k12 sync` | Verify + prune ChatGPT (codex) connections | `kancahub k12 sync --prune` |
| `kancahub k12 modes` | Show the connection modes (maps to `run_cmd.bat` [1]–[13]) | `kancahub k12 modes` |
| `kancahub k12 link-finder` | Find SheerID verification links (passthrough to `scripts/sheerid_link_finder.py`) | `kancahub k12 link-finder -- --help` |

`verify` flags map 1:1 to the original tool's connection modes:

| `run_cmd.bat` | Mode | `kancahub k12 verify` flag |
|---|---|---|
| [1] | direct + temp email | *(none — default)* |
| [2] | proxy ip:port | `--proxy IP:PORT` |
| [3] | proxy auth | `--proxy user:pass@IP:PORT` |
| [4] | debug, no proxy | `--debug` |
| [5] | debug + proxy | `--debug --proxy IP:PORT` |
| [6] | debug + proxy auth | `--debug --proxy user:pass@IP:PORT` |
| [7] | no temp email | `--no-temp-email` |
| [8] | no temp + proxy | `--no-temp-email --proxy IP:PORT` |
| [9] | no temp + proxy auth | `--no-temp-email --proxy user:pass@IP:PORT` |
| [10] | manual email | `--email you@x.com` |
| [11] | manual email + proxy | `--email you@x.com --proxy IP:PORT` |
| [12] | manual email + proxy auth | `--email you@x.com --proxy user:pass@IP:PORT` |
| [13] | exit | *(n/a)* |
| — | local gateway (wrapper extra) | `--gateway` (= `--proxy 127.0.0.1:8888`) |
| — | prompt for email at runtime (script.py) | `--ask-email` |

All 12 connection modes are reachable from `kancahub k12 verify`; the difference
between e.g. [2]/[8] is just the combination of `--proxy` with
`--no-temp-email`/`--email`. `--ask-email` is an extra interactive flag that
`run_cmd.bat` does not expose.

> **Documents at the SheerID `docUpload` step are now yowes-generated.** The K-12
> verifier no longer uploads the primitive 500×350 badge (~11 KB). It renders a
> real A4 employment letter / teacher ID / license via `scripts/yowes_docs.py`
> (employment letter ≈ **135 KB**, teacher ID ≈ 175 KB, license ≈ 147 KB) and
> falls back to the old badge only if yowes is unavailable. See
> **[YOWES_SHEERID_BRIDGE.md](YOWES_SHEERID_BRIDGE.md)**.

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
- `kancahub k12 modes` prints 8 rows (modes [1][2][3][4][7][10] + gateway + auto),
  not all 13 `run_cmd.bat` entries — but every mode **is** reachable: modes
  [5][6][8][9][11][12] are just `--debug` / `--no-temp-email` / `--email`
  combined with `--proxy` (see the parity table above). Cosmetic only; the
  wrapper logic is unchanged.
