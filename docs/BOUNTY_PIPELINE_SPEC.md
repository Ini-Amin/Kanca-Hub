# Autonomous Bug Bounty Pipeline on `blu` — Architecture Spec & Execution To-Do List

This document specifies the current state, flaws audited and fixed, and the remaining self-contained tasks to complete the **Agentic-Bug-Hunter + pentest-agent (`SKILLMAP.md`) + `grok-bot` control** pipeline on `blu` (`100.120.116.62`).

---

## 1. System Topology & Paths

- **Execution Host (`blu`):** Ubuntu 24.04 sandbox (`100.120.116.62`), ephemeral across Manus rebuilds. Durable backup pulled by `asus` (`100.104.217.54:~/.blu-backup/`).
- **Farm Worker (`lightvela`):** Runs headless browser farming (`scripts/r9_farm_remote.sh` triggered by `blu-r9-farm.timer` on `blu`) and injects fresh keys into `blu`'s 9router (`127.0.0.1:20128`).
- **Model Gateway on `blu`:** `blu-9router.service` (`http://127.0.0.1:20128/v1`), API key at `/root/grokbot/router.key`.
  - Healthy worker models: `Zcode/GLM-5.3` (via `blu-zcode-bridge` `:8787` → `blu-egress` `:25346` HK relay), `THK/deepseek-v4.1-flash:free`.
  - Spec/interactive model: `argon` (`blu-argon-bridge` `:8788`).
  - Known dead upstream: `cbai/deepseek-v4.1-flash` (all 3 `codebuddy-intl` accounts return HTTP 502).
- **Core Repositories on `blu`:**
  - `/root/Auto-FreeCF` (public repo `git@github.com:Ini-Amin/Kanca-Hub.git`): discovery, scope parsing, systemd timers, `gigs_api.py` (`:8795`), `grokbot-shim` (`:8790`/`:8791`), Telegram notifier (`scripts/notify.py`).
  - `/root/Agentic-Bug-Hunter` (deployed via `scripts/abh_deploy.sh` + `patches/agentic-bug-hunter-blu.patch`): 9-phase `recon_engine.sh`, `ScopeChecker`, `AutopilotGuard`, ReAct `agent.py`, 7-Question Gate (`brain.py`), 48 specialized scanners in `bughunter/tools/`, and `bughunter/mcp/bughunter-mcp/`.
  - `drost-ai` Docker container (`ghcr.io/drost-ai/drost-community:1.0.0`): 159 MCP tools (`docker exec -i drost-ai drost-mcp`).

---

## 2. Audit of Work Done & Flaws Fixed

### Completed & Verified
1. **9router SSE Trailer Fix (`bughunter/brain.py`, `bughunter/agent.py`):** 9router returns `content-type: text/event-stream` with a trailing `data: [DONE]` on non-streaming calls. Switched to ` json.JSONDecoder().raw_decode()` so responses are not discarded.
2. **OpenAI-Compatible Client & Model Failover (`bughunter/agent.py`, `bughunter/brain.py`):** Removed hard Ollama dependency; added `BOUNTY_MODEL_FALLBACKS` (`Zcode/GLM-5.3,THK/deepseek-v4.1-flash:free`) to both `LLMClient.chat()` and `OpenAICompatClient.chat()`.
3. **Bounded Recon Timeouts (`bughunter/tools/recon_engine.sh`):** Added `--connect-timeout`/`--max-time` on Wayback/crt.sh `curl` calls and `timeout 300 nmap ... --host-timeout 240s --max-retries 1`.
4. **Deterministic Scope Specificity & Table Boundary (`scripts/bounty_run.py`):**
   - Specificity rules (`_specificity`) ensure explicit subdomain allows (`app.launchdarkly.com`) beat apex denies (`launchdarkly.com`), while ties fail closed.
   - `parse_scope()` closes target tables at prose boundaries (>40 chars with whitespace) so hostnames mentioned in "Focus Areas" / "Known Issues" prose are not misclassified as exclusions.
   - `_is_placeholder_host()` rejects templated hosts (`bugcrowd-pam-###`) and file-extension false positives (`moment.js`).
   - 62 assertions in `tests/test_bounty_scope.py` pass.

### Flaws Found in Our Bridge (`scripts/abh_hunt.py`) & Fixed Immediately
1. **Flaw A — Unexpanded `$PATH` wiped `/usr/bin` and `/bin`:**
   - *Symptom:* `rapyd` and `openai-safety` finished in 53s with `rc=0 steps=0`.
   - *Cause:* `abh_env()` read `export PATH=/root/go/bin:/root/.local/bin:$PATH` from `/root/abh.env` literally without expanding `$PATH`. Every `run_recon` call raised `[Errno 2] No such file or directory: 'bash'`.
   - *Fix:* `abh_env()` now expands `$PATH` and `${PATH}` against `os.environ["PATH"]`.
2. **Flaw B — `steps=0` runs permanently retired programs:**
   - *Symptom:* `abh_hunt.py` checked `if rc == 0: B._mark_attempted(...)`. Because `agent.py` exits `0` even when every tool call fails (`steps=0`), broken runs permanently retired targets into `.attempted-bugcrowd`.
   - *Fix:* `abh_hunt.py` now requires `completed = (rc == 0 and got["steps"] > 0)` before marking a program attempted; `steps == 0` is reported as `hunt-empty` and kept in rotation. `rapyd` and `openai-safety` were un-retired on `blu`.
3. **Flaw C — `--platform hackerone|both` ignored HackerOne:**
   - *Symptom:* `abh_hunt.py` always called `B.discover_bugcrowd(...)` even when `--platform both` or `--platform hackerone` was passed.
   - *Fix:* Wired `B.discover_hackerone(a.limit)` into candidate discovery.
4. **Flaw D — Alphabetical seed selection picked obscure subdomains:**
   - *Symptom:* `seeds[0]` picked `badger.it.vinted.com` instead of `www.vinted.com`.
   - *Fix:* Seeds are now sorted to prefer apex (`example.com`), then `www.example.com`, then other subdomains.

---

## 3. Execution To-Do List (Self-Contained Tasks for Other Models)

Execute these tasks **in order**. Remember: **run subagents serially (1 at a time)** to avoid 429 `bridge busy` on `blu`.

---

### TASK 1: Upgrade `abh_hunt.py` from Flat ReAct Loop to the Full Staged Flow (`Agentic-Bug-Hunter` + `SKILLMAP.md`)

**Goal:** Right now `abh_hunt.py` only runs `bughunter/agent.py` for 10 steps (`--hours 0.5 --steps 10`), and `run_recon` alone takes 10–20 minutes on large scopes. Upgrade the per-program flow in `/home/amen/Auto-FreeCF/scripts/abh_hunt.py` to execute the 5-stage pipeline defined in `docs/SKILLMAP.md` and `bughunter/agents/`:

1. **Stage 1 — Multi-Seed Recon (`recon_engine.sh --quick`):**
   - Write all in-scope enumerable seeds (`seeds = B._scannable(ins, ins, outs)`) to `/root/Agentic-Bug-Hunter/bughunter/recon/<target>/seeds.txt`.
     *Corrected from `<slug>`:* every recon tool resolves paths through the target (`hunt._resolve_recon_dir(domain)` → `bughunter/recon/<domain>/`), so a slug-keyed file would be written and never read. The seed list has to sit next to the recon output it feeds for a resumed tick to find it.
   - Pass `--quick` on the initial automated tick (caps nuclei timeout at 600s instead of 1800s so recon finishes within 8–10 minutes and leaves time for hunting).
   - Expose a `quick: bool = True` parameter in `bughunter/agent.py`'s `run_recon` dispatch (`agent.py:743`) or pass `ABH_QUICK_RECON=1` via environment.
2. **Stage 2 — Surface Ranking (`recon-ranker`):**
   - After `run_recon` completes, call `bughunter/tools/target_selector.py` or `Brain.analyze_recon()` (`bughunter/brain.py`) to rank the top 5 high-value live endpoints (auth, API, GraphQL, file upload, webhook, OAuth) and inject them into `self.memory.working_memory` before step 2 of `agent.py`.
3. **Stage 3 — Vulnerability-Class Hunters (`bughunter/tools/`):**
   - Register the existing specialized scanners in `/home/amen/Agentic-Bug-Hunter/bughunter/tools/` into `TOOLS` and `ToolDispatcher.dispatch()` in `bughunter/agent.py`:
     - `cors_scanner.py` (`run_cors_scan`)
     - `jwt_scanner.py` (`run_jwt_scan`)
     - `crlf_scanner.py` (`run_crlf_scan`)
     - `nosqli_scanner.py` (`run_nosqli_scan`)
     - `graphql_audit.sh` (`run_graphql_audit`)
     - `takeover_scanner.sh` (`run_takeover_scan`)
     - `bypass_403.sh` (`run_403_bypass`)
   - Ensure each new tool name is added to `ToolDispatcher.NETWORK_TOOLS` (`bughunter/agent.py:610`) so `ScopeChecker` and `AutopilotGuard` enforce scope on every call.
4. **Stage 4 — The 4 Mandatory Pre-Submit Gates (`docs/SKILLMAP.md`):**
   - Create a function `run_four_gates(finding: dict, ins: list[str], outs: list[str], program: str) -> tuple[bool, str]` in `scripts/abh_hunt.py` (or `bughunter/tools/validate.py`):
     - **Gate 1 (`SCOPE`):** Verify affected host/URL passes `B.host_in_scope(host, ins, outs)` and `ScopeChecker.is_in_scope(url)`.
     - **Gate 2 (`DUPCHECK`):** Query `~/.config/auto-freecf/cve.db` (if CVE-based) and `check_h1_dups()` in `bughunter/tools/validate.py:154` + local job findings to reject duplicates.
     - **Gate 3 (`VM-PoC`):** Run `bughunter/tools/poc_bundler.py` to replay the raw HTTP request/curl on `blu` and verify the response still reproduces the indicator.
     - **Gate 4 (`CHALLENGER`):** Call `Brain.validate_finding()` (`7-Question Gate`) using a **different model family** than the hunter (e.g., if `Zcode/GLM-5.3` found it, challenge with `THK/deepseek-v4.1-flash:free`). Require `DECISION: SUBMIT`.
5. **Stage 5 — Report & Telegram Escalation:**
   - Only when all 4 gates pass, generate the report via `Brain.write_report()` (`bughunter/brain.py`), save to `/root/.config/auto-freecf/bounty/<job-id>/report.md`, and send a high-priority Telegram alert via `notify.send(title="CONFIRMED FINDING", ...)`.

**Verification Command:**
```bash
python3 scripts/abh_hunt.py --platform bugcrowd --max-programs 1 --hours 0.5 --steps 8
```
Confirm `steps > 0` in stdout and `agent_trace.jsonl` shows tool calls completing without `[Errno 2]`.

**Status: COMPLETE** — all five stages implemented and verified on `blu`:

| Stage | Implementation | Evidence |
|---|---|---|
| 1 Multi-seed recon | `quick: bool = True` on the `run_recon` dispatch + `recon_engine.sh --quick`; seeds written to `recon/<target>/seeds.txt` before the engine starts | `tests/test_tool_integrity.py` ALL PASS on `blu` |
| 2 Surface ranking | `SURFACE_WEIGHTS` + pure `rank_surface()` / `format_surface_rank()`, injected into `working_memory` and the observation right after `run_recon` (replaces the previous block, never duplicates) | `tests/test_surface_rank.py` ALL PASS on `blu` |
| 3 Class scanners | 9 registered: `run_cors_check`, `run_jwt_audit`, `run_crlf_scan`, `run_nosqli_scan`, `run_graphql_audit`, `run_takeover_scan`, `run_403_bypass`, `run_cve_hunt`, `run_eol_check`; new `_live_url_file` / `_run_script` / `_run_py` wrappers in `tools/hunt.py` | 17 TOOLS entries live on `blu`; dispatcher test 9 passed |
| 4 Four gates | `run_four_gates()` in `scripts/abh_hunt.py`: SCOPE → DUPCHECK → VM-PoC → CHALLENGER, cheapest-first **and** safety-ordered (SCOPE before the gate that sends traffic), each short-circuiting on failure | `tests/test_four_gates.py` ALL PASS locally and on `blu` |
| 5 Report + Telegram | `stage5_report()` writes `~/.config/auto-freecf/bounty/<job>/report.md`, appends `_ledger.json`, alerts via `notify.send()`; refuses to run without a 4/4 gate log | same test file |

**Remaining risks (out of Task 1 scope, logged not fixed):**
- Upstream `awarexone/Agentic-Bug-Hunter` **force-pushed** (`cd58a40` → `40b03ee`, 16 commits). `scripts/abh_deploy.sh` clones upstream then `git apply`s the patch, so on a *fresh* box it may now fail with "Re-derive it". `blu` is deployed by `scp` of the individual files instead, which is verified working; the patch is regenerated from the local tree as the reviewable artifact.
- `blu` had no `pytest`, so the dispatcher gate could not run there. Installed to `~/.local` only (`pip3 install --user --break-system-packages pytest`) — system site-packages untouched.
- `tests/test_hackerone_server.py` has 2 pre-existing failures from an uncommitted `server.py` change dated 2026-10-07 (`hacktivity_items` → `reports`) that the tests were not updated for, and `test_safe_http_redirects.py::test_redirect_to_public_host_is_followed` hangs under the tailnet resolver. Both are unrelated to Task 1; the suite runs with that test deselected (858 passed, 2 pre-existing failures).

---

### TASK 2: Wire Drost MCP (`drost-ai` Container) into `Agentic-Bug-Hunter`

**Goal:** `blu` runs `drost-ai` (`docker exec -i drost-ai drost-mcp`) with 159 security tools (107 external binaries pre-installed in-container), but `bughunter/agent.py` cannot call it.

**Files to Modify:**
- `/home/amen/Agentic-Bug-Hunter/bughunter/agent.py`
- `/home/amen/Auto-FreeCF/patches/agentic-bug-hunter-blu.patch`

**Implementation Steps:**
1. Add a thin stdio JSON-RPC helper `call_drost_mcp(tool_name: str, arguments: dict, timeout: int = 300) -> str` in `bughunter/agent.py` (using `subprocess.run(["docker", "exec", "-i", "drost-ai", "drost-mcp"], ...)` or the `mcp` Python package already installed in `/root/Agentic-Bug-Hunter/.venv`).
2. Expose a `run_drost_tool` function in `TOOLS` (`bughunter/agent.py`) that accepts `tool_name` and `target_url` / `args`, gated by `self._guard.check_request("GET", target_url)` in `ToolDispatcher.dispatch()`.
3. Regenerate `/home/amen/Auto-FreeCF/patches/agentic-bug-hunter-blu.patch` and deploy to `blu`.

**Verification Command:**
```bash
ssh blu 'docker ps --filter name=drost-ai --format "{{.Status}}"'
python3 tests/test_drost_bridge.py            # offline, no container needed
ssh blu 'python3 /tmp/drost_live_smoke.py'    # live, real container
```

**Status: COMPLETE.**

`call_drost_mcp()` + `run_drost_tool` in `bughunter/agent.py`. The MCP server is
`Drost AI v1.22.0`, exposing **159 tools**. Probing it first changed the design in
two ways that guessing would have got wrong:

- **Schemas are not uniform.** The exec-style tools (`drost_nmap`, `drost_ffuf`,
  `drost_httpx`, ...) take `{engagement_id, arguments}`. But `drost_cve_lookup`
  takes `cve_id` and `drost_engagement_create` takes `name`/`targets` — passing
  an unexpected `engagement_id` to those is a pydantic validation error, not a
  harmless extra key. So `engagement_id` is injected **only** when that tool's
  own schema declares it.
- **`engagement_id` must be server-generated.** A hand-made id is rejected
  outright (`"must be a server-generated identifier such as eng_2026…"`). One
  engagement is created per target on first use and cached in
  `_DROST_ENGAGEMENTS`, so a scan does not create one per call.

Three restrictions, all for the same reason — the agent is unattended **and** it
reads attacker-controlled pages, so any string the model parses from a target is
a place an attacker can put instructions:

| Restriction | Why | Enforced where |
|---|---|---|
| `DROST_DENIED` (`drost_bash`, `drost_execute`) | They run an arbitrary command line. After the model has read a target's HTML, one injected string becomes container code execution. Refused by name, **and again inside `call_drost_mcp`** so calling the bridge directly cannot reach them. | `_run_drost_tool`, `call_drost_mcp` |
| `DROST_ACTIVE` (sqlmap, dalfox, xsser, zap, attack_chain, exploit_search, evil_winrm, dotdotpwn, aircrack/aireplay/airodump) | They attack rather than enumerate. An unattended run asks first, exactly like the POST-declared tools (SECURITY-REVIEW-2026-08-22 #7). | `_run_drost_tool` |
| Per-host scope check | The model can aim a scanner at *anything* by putting the host in an argument instead of the target, so every host-shaped value in the argument tree is checked — not just `--target`. Dotted filenames (`moment.js`, `report.pdf`) are excluded so they do not cause false blocks, the same false positive `bounty_run._is_placeholder_host()` exists for. | `_drost_scope_violations` |

`run_drost_tool` carries `TOOL_METHODS = "GET"` because it is a router, not a
single scanner, so no one verb describes it. That label is deliberately **not**
the whole policy: the subset that actively attacks is refused by name before any
container call is made.

**Verified:** `tests/test_drost_bridge.py` ALL PASS locally and on `blu`
(13 sections, fully offline — the container is faked). Live on `blu`:
`drost_cve_lookup` returned real NVD data, `drost_nmap` actually executed
(`"available": true`, argv `["/usr/bin/nmap","-Pn","-p","22,80","127.0.0.1"]`),
the engagement was reused across calls, and an unknown tool name was reported
rather than crashing. `test_tool_integrity.py` now also proves that an
agent-implemented tool is *routed from* `dispatch()`, so the allowlist cannot be
used to advertise a tool nobody calls.

---

### TASK 3: Expand Multi-Platform Discovery (Intigriti & YesWeHack)

**Goal:** Requirement states providers must include **HackerOne, Bugcrowd, Intigriti, YesWeHack**. Currently `bounty_run.py` and `abh_hunt.py` only implement `discover_bugcrowd` and `discover_hackerone` (`gig_finder.py` has scrapers/feeds for additional platforms).

**Files to Modify:**
- `/home/amen/Auto-FreeCF/scripts/bounty_run.py`
- `/home/amen/Auto-FreeCF/scripts/abh_hunt.py`
- `/home/amen/Auto-FreeCF/tests/test_bounty_scope.py`

**Implementation Steps:**
1. Add `discover_intigriti(limit)` and `discover_yeswehack(limit)` + `get_scope("intigriti"|"yeswehack", slug)` in `scripts/bounty_run.py` (using public program directory JSON endpoints / `bounty_platform.py` headless fetch).
2. Update `--platform` choices in `scripts/bounty_run.py` and `scripts/abh_hunt.py` to `["bugcrowd", "hackerone", "intigriti", "yeswehack", "all", "both"]`.
3. Add unit tests for Intigriti and YesWeHack scope parsing in `tests/test_bounty_scope.py`.

**Verification Command:**
```bash
python3 scripts/bounty_run.py discover --platform all --limit 5
python3 tests/test_bounty_scope.py
```

---

### TASK 4: Upgrade `gigs_api.py` (`:8795`) to Drive ABH Sessions & Wire into `grok-bot`

**Goal:** Make the 24/7 ABH bounty pipeline controllable from the `grok-bot` UI on `blu` without SSH.

**Current Flaw in `scripts/gigs_api.py`:**
- `gigs_api.py` only looks at `/root/.config/auto-freecf/bounty/` and invokes legacy `bounty_run.py dispatch|triage|report`. It cannot see `/root/Agentic-Bug-Hunter/bughunter/recon/<target>/sessions/` or trigger `abh_hunt.py` / inject operator guidance (`agent_bump.txt`).

**Files to Modify:**
- `/home/amen/Auto-FreeCF/scripts/gigs_api.py`
- `/home/amen/Auto-FreeCF/grokbot-shim/proxy.js`

**Implementation Steps:**
1. **Add ABH endpoints to `scripts/gigs_api.py`:**
   - `GET /gigs/abh/sessions` — list active and recent ABH hunt sessions under `/root/Agentic-Bug-Hunter/bughunter/recon/*/sessions/*`, returning target, step count, findings count, last tool call from `agent_trace.jsonl`, and whether `agent.py` is currently running.
   - `POST /gigs/abh/hunt` — trigger `abh_hunt.py` asynchronously (with 900s cooldown) for a chosen platform or `--target` / `--program`.
   - `POST /gigs/abh/bump` — write operator guidance text into the active session's `agent_bump.txt` (`bughunter/agent.py:1439` already polls `agent_bump.txt` on every step and injects `[OPERATOR GUIDANCE]` into working memory!).
2. **Expose `/gigs/*` slash commands in `grokbot-shim/proxy.js`:**
   - In `grokbot-shim/proxy.js` (where chat messages to the `9router` bot are intercepted before forwarding to `127.0.0.1:20128`), intercept user messages starting with `/gigs`:
     - `/gigs status` or `/gigs jobs` → calls `GET http://127.0.0.1:8795/gigs/abh/sessions` + `/gigs/jobs` and returns a formatted Markdown status table directly in the `grok-bot` chat stream.
     - `/gigs discover [platform]` → calls `POST http://127.0.0.1:8795/gigs/discover`.
     - `/gigs hunt [program]` → calls `POST http://127.0.0.1:8795/gigs/abh/hunt`.
     - `/gigs bump <guidance>` → calls `POST http://127.0.0.1:8795/gigs/abh/bump` to steer the live agent mid-hunt from `grok-bot`.
     - `/gigs findings <id>` → calls `GET http://127.0.0.1:8795/gigs/findings/<id>`.
3. Restart `blu-gigs-api.service` and `grokbot-shim.service` on `blu`.

**Verification Command:**
```bash
ssh blu 'curl -s http://127.0.0.1:8795/gigs/abh/sessions | jq .'
```

---

### TASK 5: End-to-End Persistence & Backup Sync Check

**Goal:** Ensure every patch, ABH session finding, and systemd unit survives a `blu` Manus rebuild.

**Files to Modify:**
- `/home/amen/Auto-FreeCF/scripts/blu_bootstrap.sh`

**Implementation Steps:**
1. Ensure `scripts/blu_bootstrap.sh` calls `scripts/abh_deploy.sh` and installs `blu-bounty.{service,timer}` and `blu-r9-farm.{service,timer}` automatically on a fresh box.
2. Ensure the `asus` pull-backup includes `/root/Agentic-Bug-Hunter/bughunter/recon/*/findings/` and `/root/.config/auto-freecf/bounty/`.
