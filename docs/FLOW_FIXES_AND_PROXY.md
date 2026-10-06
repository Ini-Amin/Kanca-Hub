# Flow fixes + proxy wiring (from live run 2026-10-06)

## Two REAL bugs found by a live end-to-end run

### BUG 1 — false success: `thk batch` returns exit 0 when 0 accounts created
- Live output: `✓ 0/1 accounts created` then the pipeline printed
  `✓ Step 2 complete ... completed successfully` and `✓ End-to-end ... completed successfully!`,
  then injected/synced a STALE key. **Nothing was created.**
- Root cause: `harbor/tools/tokenharbor/cli.py::_run_batch` ends with `return 0` unconditionally.
- The pipeline (`kancahub.py` end-to-end, ~line 2872) only checks `rc_farm != 0`.
- FIX: `_run_batch` must `return 0 if created else 1` (and `_run_full_setup` similarly).
  Then the pipeline stops honestly.

### BUG 2 — TokenHarbor signup rejects our egress
- Live error: "Your IP or email provider is not supported for Token Harbor accounts. ...
  we see the country your connection exits from."
- Cause: the proxy used was a datacenter/pool IP whose exit country is flagged.
- FIX: thk must run through a CLEAN/RESIDENTIAL egress (kancahub proxy residential / WARP),
  not the raw pool. Wire `--proxy auto` to prefer residential for thk.

## PetaniProxy / kancahub proxy features (from README + GUIDE)
- `--serve 8888`  → local rotating gateway + REST API (`/api/random`, `/api/all`, `/api/status`).
- `--daemon-gateway` → 24/7 resilient gateway (auto-healer) — **runs in foreground by default**
  (the "needs another terminal / can't background" UX problem the user flagged).
- `--webshare N` → Webshare residential hunter (best vs anti-bot).
- `-C/--warp` → Cloudflare WARP (free, weaker).
- `-F/--fast-harvest` → aiohttp fast harvester; `--harvest`/`-p/--protocol/--country` filters.
- `-P/--pipeline` → async Webshare + Grok farm in parallel.
- REST endpoints let a bot pull one live proxy per request → good for per-account rotation.

### UX FIX needed: background the gateway
`proxy start`/`daemon` block the terminal. Add a `--background`/`-b` that spawns the gateway
detached, writes the port to a state file, and returns immediately — so the pipeline (and the
user) can continue in ONE terminal.

## The unified Universal flow (user's spec)
`kancahub autofarm <url>` should:
  1. Ask for the target URL.
  2. **Inspect the signup page**: detect auth methods — GitHub? Google? email? (temp email OK?)
  3. Choose the CORRECT proxy for that site:
     - GitHub-needing sites / TokenHarbor / Google → **residential** first, else WARP.
     - Sites that accept any proxy (thk, grok when working) → any working proxy.
     - If a step errors (403/challenge) → **escalate** residential/WARP, else report.
  4. Execute: GitHub OAuth if available (via stored account) → else temp-mail signup on our domain.
  5. Wait for verification mail from the relay; confirm honestly; inject to 9Router if applicable.
  6. Report exactly what happened (never "success" without an account/session).

## Tasks
- F1 (worker7): fix `_run_batch`/`_run_full_setup` exit codes + add tests (harbor repo).
- F2 (worker5): `kancahub proxy --background` (detached gateway) + state file + tests.
- F3 (worker7): wire thk + github + gmail to prefer RESIDENTIAL via auto_egress; add
  `EGRESS_TARGETS` residential-first for anti-bot-sensitive sites.
- F4 (me): rewrite autofarm to the 6-step universal flow (inspect → pick proxy → execute → verify).
- F5 (worker3): pipeline honesty — stop on real failure + report created count.
