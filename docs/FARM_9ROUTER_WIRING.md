# Wiring the tokenmix + zerotwo farms to kancahub proxy and 9Router

Status: wrapper + docs landed on `feature/farm-9router-wiring`. **No live
account was created.** The Cloudflare tunnel is not set up yet — everything
below uses `localhost`.

## 1. What the wrapper does

`scripts/farm_9router.py` is one CLI over two farms:

| Subcommand | Farm | Child process |
| --- | --- | --- |
| `tokenmix` | tokenmix-bulk-creator (`~/tokenmix-bulk-creator`) | `.venv/bin/python -m tokenmix_bulk` |
| `zerotwo` | zt-harvester (`~/zt-harvester`) | `.venv/bin/python -m ztharvester.cli run` |

Shared flags (both subcommands):

| Flag | Default | Meaning |
| --- | --- | --- |
| `-n/--count` | `1` | accounts to create |
| `--proxy` | `auto` | `auto` (smart resolve) · `none` (direct) · `URL` |
| `--router-url` | `http://localhost:20128/v1` | 9Router URL |
| `--shim-port` | `8787` | zt-harvester shim port |
| `--dry-run` | off | resolve egress + print child argv, run nothing |

Exact commands:

```bash
# dry-run first (resolves egress for real, prints the child argv, runs nothing)
python3 scripts/farm_9router.py tokenmix -n 3 --dry-run
python3 scripts/farm_9router.py zerotwo  -n 1 --dry-run

# live (starts a pool gateway when --proxy auto resolves one)
python3 scripts/farm_9router.py tokenmix -n 3
python3 scripts/farm_9router.py zerotwo  -n 1
```

Child argv it builds (verified output of `--dry-run`):

```
tokenmix: /home/amen/tokenmix-bulk-creator/.venv/bin/python -m tokenmix_bulk \
            -n 3 -c 1 --delay-min 20 --delay-max 45 [--proxy http://user:pass@host:port]

zerotwo:  /home/amen/zt-harvester/.venv/bin/python -m ztharvester.cli run \
            -n 1 --router-url http://localhost:20128 \
            --shim-base-url http://localhost:8787/v1 \
            --concurrency 1 [--proxy host:port:user:pass]
```

Format note: tokenmix (Playwright) takes a full proxy URL
(`http://user:pass@host:port`); zt-harvester's `Proxy.parse` only understands
`host:port[:user:pass]`, so the wrapper converts the resolved URL for it.
`--router-url`'s `/v1` suffix is stripped for zt-harvester because its own
management client appends `/api/...` to the base.

## 2. (a) kancahub proxy egress

`--proxy auto` resolves through `scripts/egress.py`:

1. `auto_egress(target_url, mode="auto")` when this checkout has it (it lands
   with `feature/kancahub-proxy-autowire`: local gateway :8888/:8899 → pool
   gateway → WARP → residential → direct). The wrapper probes the farm's
   signup URL: `https://tokenmix.ai` for tokenmix, `https://app.zerotwo.ai`
   for zerotwo.
2. Fallback on this branch: `ensure_clean_egress(prefer_pool=
   signup_from_scratch/proxies.txt)` → local rotating gateway.

Verified dry-run (zerotwo):

```
[egress] Real / blocked IP: 103.147.251.203
[egress] Checking health of preferred pool .../signup_from_scratch/proxies.txt…
[egress] ✓ Using 10 verified proxies from preferred pool
[egress] ✓ Clean egress gateway ready: http://127.0.0.1:48789 (exit IP: 31.58.9.4)
[proxy] auto:pool_gateway -> http://127.0.0.1:48789
[farm] ... --proxy 127.0.0.1:48789
```

Gateways spawned by the wrapper are stopped when the child exits (and in
`--dry-run` right after the preview). `--proxy none` forces direct;
`--proxy http://host:port` is passed through verbatim (scheme added if
missing).

## 3. (b) 9Router at :20128

9Router runs locally (verified: `next-server` on :20128, 51 connections, 10
nodes). Two surfaces:

- **Management API** `http://localhost:20128/api/*` (provider nodes +
  connections). Auth on this build is
  `x-9r-cli-token: sha256(machine-id + "9r-cli-auth" + cli-secret)[:16]`
  (`~/.9router/machine-id`, `~/.9router/auth/cli-secret`).
- **OpenAI surface** `http://localhost:20128/v1` (`/v1/models`,
  `/v1/chat/completions`).

Per farm:

**zerotwo** — zt-harvester routes natively: `engine.run()` ensures a
`openai-compatible` provider node (prefix `zerotwo`, baseUrl
`http://localhost:8787/v1`) and POSTs one connection per harvested account
(`provider=openai-compatible-zerotwo`, `apiKey=<JWT>`). The shim
(`zt-harvester shim --port 8787`) must be running; start it before the farm:

```bash
cd ~/zt-harvester && .venv/bin/zt-harvester shim --port 8787 &
python3 /home/amen/Auto-FreeCF/scripts/farm_9router.py zerotwo -n 1
```

**tokenmix** — the creator has **no 9Router code**; it produces
`accounts.json` with `api_key` fields (OpenAI-compatible at
`https://api.tokenmix.ai/v1`). Registering tokenmix into 9Router is a manual
one-time step:

```bash
# 1. create a provider node (management API, x-9r-cli-token auth)
#    {type:"openai-compatible", name:"TokenMix", baseUrl:"https://api.tokenmix.ai/v1",
#     prefix:"tokenmix", apiType:"chat"}
# 2. for each harvested api_key, POST /api/providers with
#    provider="openai-compatible-tokenmix", apiKey=<key>, name="TokenMix · <email>"
```

No tokenmix node exists in 9Router yet (verified: prefixes are
evomap/nr/THK/kie/AG/jdw/vs/tia/zanslab/apmix). Until the keys are registered,
tokenmix output is only as good as its JSON file.

## 4. Honest blockers

- **zerotwo — Cloudflare on app.zerotwo.ai (primary).** A headless visit
  renders `Just a moment...` / `Performing security verification`, no `#email`
  input (verified 2026-10-06, Ray ID recorded). The signup flow cannot start
  until an interactive session solves it; the same `cf_clearance` cookie is
  what the shim needs for its upstream calls.
- **zerotwo — router9 handshake mismatch.** On this 9Router build the
  management API rejects `Authorization: Bearer …` with 401 (verified with
  the live instance; correct header is `x-9r-cli-token`). zt-harvester's
  `router9.py` also sends `apiType:"openai"` (route accepts only
  `chat|responses`) and reads the node id from the wrong response field
  (`{"node":{...}}`). Result today: sessions would be saved to
  `harvest/sessions.jsonl` but **not routed** until `router9.py` is fixed.
- **tokenmix — disposable-domain block.** TokenMix rejects many throwaway-mail
  domains (`400 This email provider is not supported. Please use a mainstream
  or work email`); the run marks the account failed as `DomainNotSupported`
  and does not retry that domain. Mitigations: try the other provider
  (`--mail-provider mail-tm`), pin a domain, point `--mail-base-url` at a
  controlled provider, or slow down (global 429 rate limit also exists).
- **Both** — datacenter proxy ranges (`31.58/31.59/45.38…` in the shipped
  pool) are exactly what anti-bot systems challenge hardest; expect more
  challenges than with residential exits.

## 5. TODO — Cloudflare tunnel (later)

Goal: expose 9Router (`:20128`) publicly so remote consumers (cloud farms,
phones) can reach `…/v1` without being on this LAN.

1. `cloudflared tunnel login && cloudflared tunnel create 9router`
2. Route a hostname, e.g. `9router.example.com`, to
   `http://localhost:20128` (`cloudflared tunnel route dns` + config ingress).
3. Restrict access (Cloudflare Access policy or 9Router's own API keys) —
   do **not** expose the management `/api/*` surface unauthenticated.
4. Update consumers to the public URL:
   `scripts/farm_9router.py … --router-url https://9router.example.com/v1`.
   Note the zerotwo shim base URL stays local (it is called by 9Router on
   this host); only the farm→9Router direction changes.
5. Verify with `curl https://9router.example.com/v1/models` before enabling
   any farm run.

## 6. Tests

```bash
python3 -m py_compile scripts/farm_9router.py
python3 -m unittest tests.test_farm_9router_wiring -v   # 17 tests, no network
```

Covered: subcommands present, `--proxy` default `auto`, `--router-url` default
`http://localhost:20128/v1`, `/v1` stripping, per-farm proxy normalization,
exact child argv for both farms (with and without proxy), unknown farm
rejection, and readiness checks (missing repo/venv are reported, never faked).
