# 🌾 KancaHub

**One CLI for the whole account-farming toolkit.** Unifies Cloudflare Workers AI
farming, proxy harvesting, WARP tunnelling, xAI/Grok registration, TokenHarbor
key generation, ChatGPT K-12 teacher verification, and 13-country teacher
document generation behind a single command surface — with 9Router as the
central credential sink.

```bash
kancahub doctor          # health check across everything
kancahub stack signup -n 3 --warp    # create 3 CF accounts on clean IPs, inject to 9Router
```

---

## Why

Creating usable Workers-AI credentials used to mean juggling five repos, manual
proxy wrangling, and copy-pasting tokens into 9Router. KancaHub collapses that
into one tool with **non-overlapping** responsibilities, driven by real,
audited feature surfaces (not thin wrappers).

---

## Components

| Group | Backend | Purpose |
|---|---|---|
| `stack` | Auto-FreeCF | Cloudflare Workers AI signup / login / validate / sync / manage |
| `warp` | PetaniProxy | Cloudflare WARP WireGuard tunnel (clean, unflagged egress IPs) |
| `region` | (built-in) | Signup region profiles for promo/bonus targeting |
| `thk` | harbor | TokenHarbor account + API-key generation → 9Router |
| `grok` | grok-register | xAI/Grok account farm (SSO risk gate, 5 mail providers) |
| `proxy` | PetaniProxy | Proxy harvest / gateway / Webshare residential / stats / API |
| `k12` | ChatGPT-K-12 | SheerID teacher verification → ChatGPT accounts |
| `yowes` | yowes | 13-country teacher document generator |
| `doctor` | (built-in) | Dependency + service health check |

---

## Quick start

```bash
# 1. health check
kancahub doctor

# 2. clean egress + a targeted region
kancahub warp up
kancahub region set us

# 3. the full pipeline — create accounts, verify email, mint tokens, inject
kancahub stack signup -n 3 --warp

# 4. keep 9Router clean
kancahub stack sync --prune
```

---

## Command reference

### `stack` — Auto-FreeCF
```bash
kancahub stack signup -n 3 --warp          # create accounts + tokens, inject to 9Router
kancahub stack signup -n 3 --proxy http://127.0.0.1:8888
kancahub stack signup -n 3 --proxy-pool signup_from_scratch/proxies.txt
kancahub stack login email:pass             # existing account -> token
kancahub stack login --bulk accounts.txt --google
kancahub stack validate --token cfut_x --account-id abc123
kancahub stack sync --prune                 # verify + drop dead 9Router connections
kancahub stack manage --token-file tokens.txt --out-csv out.csv
kancahub stack web --port 8080 --open       # Flask UI
```

### `warp` — Cloudflare WARP
```bash
kancahub warp status     # tunnel state + egress IP
kancahub warp up         # generate profile + bring tunnel up
kancahub warp down
kancahub warp gen        # fresh profile only
```

### `region` — promo/bonus targeting
```bash
kancahub region list         # us uk sg id de jp in br au ca any
kancahub region set sg       # bias proxy country + WARP endpoint
kancahub region current
kancahub region clear
```

### `thk` — TokenHarbor (harbor)
```bash
kancahub thk setup           # full setup (interactive)
kancahub thk batch 3         # create N accounts -> thk_live_ keys
kancahub thk inject -i account.json --verify   # keys -> 9Router
kancahub thk sync --prune    # verify + prune TokenHarbor connections
kancahub thk test-key thk_live_xxx
```

### `grok` — xAI farm (grok-register)
```bash
kancahub grok run            # CLI registration flow
kancahub grok web            # WebUI on 127.0.0.1:8092
kancahub grok pool           # show grok2api token pool
kancahub grok retry --pending accounts_1.txt.pending.jsonl
```

### `proxy` — PetaniProxy
```bash
kancahub proxy harvest --country US --protocol socks5 --target 30
kancahub proxy daemon         # 24/7 auto-healing gateway :8888
kancahub proxy residential -n 2
kancahub proxy warp           # (internal generator; prefer `kancahub warp`)
kancahub proxy stats          # live gateway REST stats
kancahub proxy api /api/all   # hit any gateway endpoint
kancahub proxy export --to-pool
```

### `k12` — ChatGPT teacher verification
```bash
kancahub k12 auto                    # full account + OTP + SheerID verify
kancahub k12 verify <sheerid-url> --gateway
kancahub k12 modes                   # the 12 connection modes
```

### `yowes` — teacher documents
```bash
kancahub yowes list
kancahub yowes schools --country us
kancahub yowes make --country us --first John --last Doe \
    --school "Norton Elementary" --position Teacher --dob 1985-03-15
kancahub yowes k12 --first Jane --last Smith --school "Norton Elementary"
kancahub yowes gui
```

---

## Architecture

```
┌─────────────┐   ┌──────────────┐   ┌───────────────┐
│  WARP       │   │  PetaniProxy │   │  region       │
│  (clean IP) │   │  (gateway)   │   │  (promo geo)  │
└──────┬──────┘   └──────┬───────┘   └──────┬────────┘
       │                 │                  │
       └────────┬────────┴──────────────────┘
                ▼
        ┌───────────────┐        ┌──────────────────┐
        │  Auto-FreeCF  │───────►│  Supabase mail   │
        │  signup flow  │◄───────│  + CF Email Work │
        └───────┬───────┘        └──────────────────┘
                │ cfut_ tokens
                ▼
        ┌───────────────┐   ┌──────────────┐   ┌─────────────┐
        │   9Router     │◄──│  harbor thk  │   │ grok-register│
        │ (central sink)│   │  (thk_live_) │   │  (SSO pool)  │
        └───────────────┘   └──────────────┘   └─────────────┘
```

**Mail relay** — Cloudflare Email Routing (catch-all) → Email Worker →
Supabase Edge Function stores mail → the signup pipeline reads verification
links. See `supabase/` and `cloudflare/`. All free, self-hosted.

---

## Requirements

- Linux (Fedora tested), Python 3.10–3.13, Node ≥18
- Google Chrome, `wireguard-tools` (for WARP), `ffmpeg`
- The managed venv at `~/.local/share/auto-freecf/venv` (created by `moycf`)

`kancahub doctor` reports exactly what's missing.

---

## Security

- Secrets live in `~/.config/auto-freecf/.env` (perms 600), **outside** the repo.
- Real config (`wrangler.toml`, `supabase/config.toml`, `config.json`, proxy
  pools, results) is **gitignored**; only `*.example` templates are committed.
- Never commit `cfut_`, `thk_`, JWT, or session tokens.

---

## License

MIT. Each bundled tool keeps its own license.
