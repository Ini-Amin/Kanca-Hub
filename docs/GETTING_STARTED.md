# Getting Started with KancaHub

A beginner's guide to **KancaHub** — the single CLI that ties the FreeCF toolkit
together behind one `kancahub` command.

> This guide covers **legitimate, self-hosted tooling only**: using the free
> Cloudflare Workers AI tier through your own WARP tunnel, managing 9Router
> credentials, generating TokenHarbor free keys, proxy/WARP tooling, and the
> `yowes` document generator for **your own** records.

---

## 1. What is KancaHub?

KancaHub is a thin, friendly **command surface** over a set of tools that
otherwise each have their own CLI. Instead of remembering five different
entry points, you type `kancahub <group> <command>`.

It unifies:

| Group | What it gives you (legit uses) |
|---|---|
| `stack` | Create/sign-up Cloudflare accounts and mint free **Workers AI** tokens; validate, inject and sync them into 9Router. |
| `warp` | Cloudflare **WARP** WireGuard tunnel — gives you a clean egress IP so your own signups go through without being blocked. |
| `region` | Region profiles (US/UK/SG/… ) to bias WARP/proxy country for promo/bonus targeting on accounts you own. |
| `thk` | **TokenHarbor** — create free API keys and inject/sync them with 9Router. |
| `proxy` | **PetaniProxy** — harvest public proxies, run a rotating gateway, use your own residential proxies (Webshare). |
| `grok` | Grok/xAI account registration (grok-register backend). |
| `k12` | ChatGPT K-12 / SheerID teacher verification. *(Out of scope for this guide — see the K-12 docs.)* |
| `yowes` | 13-country teacher document generator — make ID cards / letters **for your own use**. |
| `doctor` | One-shot health check: files, Python modules, binaries, gateway, WARP. |

Everything writes into **9Router** (`~/.9router/db/data.sqlite`) as the central
credential store. 9Router already knows the `cloudflare-ai` provider, so no
custom provider setup is needed.

---

## 2. Install

KancaHub expects the standard Auto-FreeCF layout:

```
~/Auto-FreeCF/                      # this repo
~/.local/share/auto-freecf/venv/    # managed Python venv (created by `moycf`)
~/.local/bin/kancahub               # launcher shim on your PATH
```

### 2.1 Base requirements

- Linux (Fedora tested), Python 3.10–3.13, Node ≥18
- Google Chrome, `ffmpeg`, `git`
- `wireguard-tools` (for WARP)
- Optional: `adb`, `sing-box`

### 2.2 The `kancahub` launcher

The `kancahub` command is a tiny shell shim that runs `scripts/kancahub.py`
with the managed venv:

```bash
#!/usr/bin/env bash
VENV_PY="$HOME/.local/share/auto-freecf/venv/bin/python"
SCRIPT="$HOME/Auto-FreeCF/scripts/kancahub.py"
if [ -x "$VENV_PY" ]; then
  exec "$VENV_PY" "$SCRIPT" "$@"
else
  exec python3 "$SCRIPT" "$@"
fi
```

If `kancahub` is not on your PATH, you can always run it explicitly:

```bash
~/.local/share/auto-freecf/venv/bin/python ~/Auto-FreeCF/scripts/kancahub.py doctor
```

### 2.3 Verify the install

```bash
kancahub doctor
```

You'll get a checklist of files, Python modules, binaries and services. Fix any
`❌` before continuing.

### 2.4 Where secrets live

- `~/.config/auto-freecf/.env` (perms `600`) — secrets, **outside** the repo.
- Real config (`wrangler.toml`, `config.json`, proxy pools, results) is
  **gitignored**. Only `*.example` templates are committed.
- Never commit `cfut_`, `thk_`, JWT, or session tokens.

---

## 3. The `kancahub` command

### 3.1 Interactive menu

Run `kancahub` with **no arguments** and you get a banner plus a beginner menu:

```
kancahub
```

```
  [1] Create Cloudflare accounts + tokens     (stack signup)
  [2] Check everything is healthy             (doctor)
  [3] Start the proxy gateway                 (proxy daemon)
  [4] Create TokenHarbor keys                 (thk batch)
  [5] Grok farm                               (grok run)
  [6] K-12 teacher verification               (k12 auto)
  [7] Manage WARP tunnel                      (warp)
  [8] Help & command reference                (--help)
  [0] Exit
```

Pick a number and KancaHub runs the matching command for you.

### 3.2 Direct commands

Any time you know what you want, skip the menu:

```bash
kancahub doctor          # health check
kancahub --help          # full reference (ASCII banner + all groups)
kancahub stack --help    # options for one group
kancahub stack signup --help
```

### 3.3 `kancahub doctor`

The health check is the first thing to run and the first thing to run when
something breaks. It reports:

- **Files** — required scripts/dirs across Auto-FreeCF, PetaniProxy, K-12,
  Yowes, harbor, grok-register, 9Router DB, secrets, proxies, WARP, region.
- **Python modules** — `nodriver`, `patchright`, `httpx`, `requests`,
  `curl_cffi`, `cloudscraper`, `DrissionPage`, `speech_recognition`, `pydub`,
  `PIL`, `mcp`, `customtkinter`, `rich`, `tomllib`, `fastapi`.
- **Binaries** — `google-chrome`, `ffmpeg`, `git`, `adb`, `wg`, `sing-box`.
- **Services** — PetaniProxy gateway on `:8888` and the WARP tunnel state.

Symbols: `✅` present/working · `❌` missing (fix it) · `➖` optional/not running.

---

## 4. Cloudflare Workers AI quickstart

This is the core legitimate workflow: bring up a clean WARP egress, target a
region, create your own Cloudflare account(s), mint the free Workers AI token,
and inject it into 9Router.

```bash
kancahub warp up                      # 1. clean Cloudflare egress IP
kancahub region set us                # 2. target the US region profile
kancahub stack signup -n 1 --warp     # 3. create 1 account + token on WARP
kancahub stack sync --prune           # 4. verify + drop dead 9Router conns
```

Step by step:

1. **`kancahub warp up`** — generates a WARP WireGuard profile (honouring the
   active region) and brings the tunnel up with `wg-quick`. WARP gives an
   egress IP that is not pre-flagged, so the Cloudflare signup flow passes.

2. **`kancahub region set us`** — writes `~/.config/auto-freecf/region.json`
   with the `us` profile (country `US`, WARP endpoint `162.159.192.1:2408`).
   Other profiles: `uk sg id de jp in br au ca any`.

3. **`kancahub stack signup -n 1 --warp`** — this is the full pipeline. With
   `--warp` it brings WARP up first (and tears it down afterwards if it was
   down), warms up the egress against the signup hosts, then runs
   `scripts/pipeline.py`:

   ```
   signup  →  create Cloudflare account(s) + Workers AI token(s)
   verify  →  validate each token against Cloudflare
   inject  →  push valid keys into 9Router (cloudflare-ai provider)
   ```

   Results land in `signup_from_scratch/results.json`.
   Use `-n N` for N accounts. Add `--no-inject` for signup+verify only.

4. **`kancahub stack sync --prune`** — reads every `cloudflare-ai` connection
   from `~/.9router/db/data.sqlite`, live-tests each token against Cloudflare,
   prints a health table, and with `--prune` removes the dead ones (a timestamped
   backup is written first).

### Where the pieces live

- Signup results: `signup_from_scratch/results.json` (schema: list of dicts
  with `api_token` starting `cfut_` and an `account_id`).
- Injector: `scripts/inject_9router.py` → `providerConnections` table,
  provider `cloudflare-ai`, default model `@cf/openai/gpt-oss-120b`.
- Sync/prune: `scripts/sync_9router.py`.
- 9Router reads the DB live; restart 9Router if a new provider doesn't appear.

---

## 5. Other handy tools

### Proxy / WARP tooling

```bash
kancahub proxy harvest --country US --protocol socks5   # harvest + validate
kancahub proxy export --to-pool                          # fill Auto-FreeCF pool
kancahub proxy test                                      # validate the pool
kancahub proxy daemon                                    # 24/7 gateway on :8888
kancahub proxy residential -n 2                          # Webshare hunter
```

### TokenHarbor free keys

```bash
kancahub thk setup                 # interactive first-run setup
kancahub thk batch 3               # create N accounts → thk_live_ keys
kancahub thk inject --verify       # push keys into 9Router
kancahub thk sync --prune          # verify + prune TokenHarbor connections
```

### yowes — documents for your own use

```bash
kancahub yowes list                                   # countries + doc types
kancahub yowes schools --country us                   # schools list
kancahub yowes make --country us --first John --last Doe \
    --school "Norton Elementary"                      # generate documents
kancahub yowes gui                                    # desktop GUI
kancahub yowes mcp                                    # MCP server (stdio)
```

---

## 6. Next steps

- Full per-command reference: **[COMMANDS.md](COMMANDS.md)**
- Project overview + architecture: **[../KANCAHUB.md](../KANCAHUB.md)**
- Security notes: **[../SECURITY.md](../SECURITY.md)**

When in doubt, run `kancahub doctor` first.
