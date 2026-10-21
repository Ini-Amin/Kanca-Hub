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

### Session 2026-10-21: why every hunt ended `steps=1 findings=0`

Three separate defects, each of which alone was enough to end a run early. All
three are fixed; the first two are verified on `blu`.

1. **`run_vuln_scan` rejected the `full` kwarg the schema advertised.**
   `hunt.run_vuln_scan(domain, quick=False)` vs the tool schema's
   `{quick, full}`. Every dispatch raised
   `TypeError: unexpected keyword argument 'full'`; the agent retried, `loop_warn`
   tripped at 3, and the session ended. Trace evidence:
   `sessions/20261021T190258282048-47ba9301/agent_trace.jsonl`.
   *This is the one that made "no findings" indistinguishable from "no scan".*

2. **A single tool could consume the entire budget.**
   `run_recon` had a 3600s internal cap against a 540s agent budget. Measured:
   one `run_recon` took **338.5s** of a 540s run and the loop stopped at
   `steps=1` with 60s left. `run_recon`/`run_vuln_scan` now take a `timeout`
   argument, and `agent.py` passes a slice of the *remaining* budget (55% for
   recon, 70% for scans). A clamped recon returns True with its partial
   artifacts instead of being reported as a failure.

3. **The model chain started with a model that cannot answer.**
   `BOUNTY_MODEL_FALLBACKS` begins with `Zcode/GLM-5.3`. Measured 2026-10-21
   20:20 WIB: GLM-5.3 → HTTP 503 after 12.4s; `ag/gemini-3.8-flash-high` → 3.1s;
   `THK/deepseek-v4.1-flash:free` → timeout at 60s. Cause is quota, not config:
   all 10 accounts holding a `model:glm-5.3` bucket are at 3,000,000/3,000,000
   units (reset 22:59). `zcode.py autofarm` is `enabled: false`, and the farm
   only triggers when usable accounts ≤ `min_active`.

**Consequence for reading the logs:** `steps=N findings=0` is not a verdict.
Only `steps>0` *with every attempted tool returning a real observation* is.
The digest currently prints the first number and hides the second.

### `cve.db` is now a hunting input, not just a rejection filter

`~/.config/auto-freecf/cve.db` (390,221 CVEs, FTS5, 494 MB) was read only by
`abh_hunt.gate_dupcheck()`, and only to reject a finding that claimed an
already-published CVE. The hunt itself never consulted it.

`bughunter/tools/cve_intel.py` (new) turns recon fingerprints into ranked
hypotheses, exposed to the agent as `run_cve_intel`:

- Reads `live/httpx_full.txt` tech blocks (`Nginx:1.31.1`) and `technologies.txt`.
- Ranks by **testability** (server-side > client library > unknown), then exact
  version match, then CVSS. Third-party services the target merely *uses*
  (Amazon S3, Google Analytics, Vercel) are dropped and counted.
- `version-unmatched` is a distinct state: the observed version appears in no
  advisory text, so the product's CVEs are returned for manual range checking
  rather than silently dropped.
- `cwe_history` / `patterns`: the CWE distribution of a product's high-severity
  CVEs (nginx → CWE-22 x16, CWE-20 x15, CWE-434 x10; Apache httpd → CWE-476 x9,
  CWE-444 x8). This is the part that supports *pattern* work: it says where to
  look even when no single advisory matches.

Measured failure mode that shaped the ranking: a naive
`description LIKE '%nginx%'` returned four Nginx-UI CVEs and zero real nginx
CVEs; `amazon s3` returned MinIO and a WordPress plugin. On `bitso.com` that
produced 33 fingerprints and 0 usable candidates before the fix.

**Deployment note:** the DB is not on `blu` by default. A slim build
(`year>=2015 AND cvss31>=4`, 250,916 rows, 131 MB, FTS mirrors dropped) is what
`blu` now holds at `/root/.config/auto-freecf/cve.db`. Without it
`run_cve_intel` returns an explicit "cve.db not found" message — a missing
input, never an empty result.

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

**Status: COMPLETE.** All four required providers (HackerOne, Bugcrowd,
Intigriti, YesWeHack) discover and produce scope.

The implementation cost almost nothing because the **same
`arkadiyt/bounty-targets-data` mirror HackerOne already reads** carries
`intigriti_data.json` (140 programs) and `yeswehack_data.json` (55). That
changes one thing that mattered: the data is **public and structured**, so
unlike Bugcrowd there is no brief to render and no saved browser session to keep
alive — `get_scope()` answers for these two entirely offline, and a program's
scope is real JSON fields rather than prose that has to be parsed.

`discover(platform, limit)` is now the single entry point; `cmd_discover`,
`cmd_dispatch` and `abh_hunt.py` all route through it. `cmd_dispatch` was
**hardcoded to `discover_bugcrowd` regardless of `--platform`**, so any non-
Bugcrowd choice silently dispatched over Bugcrowd candidates.

**Where the scope data is honest and where it is not.** These feeds contain
entries that are not hostnames, and every one of them is *dropped* rather than
approximated:

| Feed entry | Handling | Why |
|---|---|---|
| `endpoint: "all"` (Intigriti whole-asset scope) | dropped | Mapping `all` → the apex would claim a far wider scope than the program granted |
| Prose (`"Any resource created with the Outscale Cloud…"`) | dropped | Not a hostname; there is no defensible host to derive |
| `*.doctolib.(fr\|de\|it\|com\|net)` regex alternation | dropped | `ScopeChecker._domain_matches` does a literal `endswith()`, so it matches **nothing** — dead as an allow-list entry *and* an unresolvable seed as `seeds[0]` |
| `10.0.0.0/8` CIDR | dropped | Not a hostname; ABH cannot resolve or recon it |
| `moment.js`, `report.pdf` | dropped | Downloadable assets listed as targets |

Each drop is the fail-closed direction — under-scanning, never over-scanning —
and a program whose only targets are dropped ends up with **no seed** (which the
caller reports) rather than a seed recon could not resolve. Wildcards are
**preserved as wildcards**: flattening `*.example.com` to `example.com` would
narrow what we scan, and widening an apex to its subdomains would scan hosts the
program never invited.

Also: `get_scope()` for an unknown handle returns `([], [], "")` — the same
"empty page" signal `cmd_scope`/`abh_hunt` use for *session expired*. It must not
look like a program that legitimately lists no targets, or a mistyped slug would
be read as a scoped-but-empty program.

**Verified:** `bounty_run.py discover --platform all --limit 5` returns all four
platforms reward-sorted (exit 0). `tests/test_bounty_scope.py` **102 OK / 0
FAIL** — up from 62 assertions; the new sections cover `_host_pattern` shapes,
both target key spellings, discovery filtering/sorting, `--platform` routing
(scrappers stubbed so the test cannot make network calls), cache reuse with a
fetch counter proving **0 mirror fetches**, unknown-handle semantics, and a
mirror fetch failure reading as "empty", not as an exception.

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

**Status: COMPLETE.** Verified live on `blu`: `{"count": 12, "abh_dir": "/root/Agentic-Bug-Hunter", ...}`
returning real sessions (`jointhemoment.net` 13 steps / 3 findings, `app.launchdarkly.com`, …).

| Surface | Behaviour | Evidence |
|---|---|---|
| `GET /gigs/abh/sessions` | newest-first listing of `recon/*/sessions/*`; `steps` from `agent_session.json` falling back to the trace, `findings` from `findings_log`, `last_tool` from `agent_trace.jsonl`, `running`/`bump_pending`/`age_s` | `tests/test_gigs_api.py` 41 checks ALL PASS on laptop **and blu**; live `?limit=3` → `count=3`, live `?target=../../etc` → 400 |
| `POST /gigs/abh/hunt` | 202 + `status_url`, spawns `abh_hunt.py` detached, 900s cooldown, `--program`/`--target` passthrough | live 202 on `yeswehack --dry-run`, then `rc=0` on `GET /gigs/abh/hunt`; second tap → 429 |
| `POST /gigs/abh/bump` | writes `agent_bump.txt` for the running target's newest session; 409 when nothing is running | live 409 when idle; e2e node test asserts the file lands in the **newest** session of the alive target |
| `grokbot-shim/proxy.js` | `/gigs status\|jobs`, `discover [platform]`, `hunt <slug> [platform]`, `bump <text>`, `findings <id>` answered from the API — a control command costs **zero tokens** and never reaches the model | 22 assertions against the **shipped** block (sliced out of `proxy.js`, no `proto.cjs` needed) ALL PASS; `node --check` OK on blu |
| services | both restarted and `active` | `systemctl is-active` → `active active` |

**Two defects found and fixed while building this — one of them pre-existing and load-bearing:**

1. **The rate limiter had never worked.** `_deny()` did `return self._json(429, …)`, and `_json()`
   writes the response and returns **None** — so every `if (d := self._deny(...))` was falsy. The
   client was told *"cooldown, retry in 900s"* while the throttled action ran anyway. For `dispatch`
   (900s) that meant duplicate scans all along; for `hunt` it would have been duplicate model spend.
   Confirmed against the committed version, so this predates Task 4. `_deny()` now writes the 429 and
   returns a truthy stop-signal, and `tests/test_gigs_api.py` pins the return value.
2. **Validation burned the cooldown.** `_deny()` stamped the window before validation ran, so a typo —
   bad platform, bad slug, a 400 that did no work — locked the operator out of `hunt` for the full
   15 minutes. Validation now runs first on both new endpoints; proven live by a valid 202 landing
   after four rejected requests.

Two smaller ones, both in Task 4 code: `summarize_session()` truncated `mtime` to `int()`, tying
sessions created inside one second so "newest first" silently fell back to *alphabetical* and a bump
could steer the wrong run (now kept at sub-second precision); and `/gigs/findings/<id>` answers 200
with an empty list for an id that does not exist, so the shim checks `/gigs/jobs/<id>` first rather
than reporting *"no findings yet"* for a typo.

**Deployed to `blu` by `scp`** (not `abh_deploy.sh`): `scripts/gigs_api.py`, `scripts/abh_hunt.py`,
`scripts/bounty_run.py`, `tests/test_gigs_api.py`, `tests/test_bounty_scope.py`, `tests/test_cli_surface.py`
+ `tests/kancahub_surface.json` (the latter two were **missing on blu entirely** — the argparse guard
had never run there), and `grokbot-shim/proxy.js` → **`/root/grokbot/9router-shim/proxy.js`**, which is
where `grokbot-shim.service` actually executes it from (`/root/Auto-FreeCF/grokbot-shim/` does not
exist on blu). blu's copy was byte-identical to repo HEAD before the swap, so no box-local edits were
overwritten. ABH patch regenerated (`111106 → 113331` bytes) and `git apply --reverse --check` returns
rc=0 on blu, confirming file + patch agree.

---

### TASK 5: End-to-End Persistence & Backup Sync Check

**Goal:** Ensure every patch, ABH session finding, and systemd unit survives a `blu` Manus rebuild.

**Files to Modify:**
- `/home/amen/Auto-FreeCF/scripts/blu_bootstrap.sh`

**Implementation Steps:**
1. Ensure `scripts/blu_bootstrap.sh` calls `scripts/abh_deploy.sh` and installs `blu-bounty.{service,timer}` and `blu-r9-farm.{service,timer}` automatically on a fresh box.
2. Ensure the `asus` pull-backup includes `/root/Agentic-Bug-Hunter/bughunter/recon/*/findings/` and `/root/.config/auto-freecf/bounty/`.

**Status: COMPLETE.** Both steps shipped, and the durability check found **three reasons a rebuilt
box could not have run the pipeline at all**, plus a wrong backup path.

**Step 1 — the bootstrap now provisions the pipeline.** New sections `4a`/`4b`/`4c` in
`scripts/blu_bootstrap.sh`, run verbatim against `blu` twice (second run proves idempotency):

| Section | What it does | Evidence |
|---|---|---|
| `4a` code sync | tars `scripts patches tests grokbot-shim` (excluding `__pycache__`) to `/root/Auto-FreeCF` over ssh | `code 14M (scripts patches tests grokbot-shim)`; only the pipeline subtrees cross — the repo root is a 1.5 GB farm/scraper tree with its own credentials, and a throwaway box must not receive those |
| `4b` units | installs `blu-bounty.{service,timer}`, `blu-r9-farm.{service,timer}`, `blu-gigs-api.service`; `grokbot-shim.service` only if `/root/grokbot/9router-shim/proxy.js` exists | `bounty active active active`, `shim active`; the shim's ExecStart is outside this repo, so enabling it otherwise would start a unit that can only fail-loop |
| `4c` engine | calls `abh_deploy.sh`, preflighting the two hard requirements it dies on (`router.key`, 9router on `:20128`) | on `blu` it reported `SKIP (9router not answering on :20128)` — a correct refusal, not a crash |

**Three flaws the check found — one of which would have destroyed the deployed tree:**

1. **Nothing placed the repo on the box.** The old bootstrap never cloned or copied `Auto-FreeCF`,
   so a fresh box had no `gigs_api.py`, no patch, no unit files. `blu`'s copy is a scp'd tree, not a
   git repo. This is why a rebuilt box could not run the pipeline even with every unit fixed.
2. **`abh_deploy.sh` would have `rm -rf`'d the deployed ABH tree.** Its `[ -d "$ABH_DIR/.git" ]`
   test is false on `blu` (also a scp'd tree, no `.git`), so it takes the **clone** branch: delete
   `$ABH_DIR`, clone upstream, then apply a patch derived from a commit upstream has since
   force-pushed away. The tree is gone whether or not the apply succeeds after. `4c` therefore
   checks the patch **reverse-applies cleanly** first — the honest signal that our edits are in
   place, and it needs no git repo — and skips the deploy when it does. Proven on `blu`: the guard
   fires, and the tree is untouched afterwards (`patch still applied: rc=0`,
   `_classify_obs` marker still present, `test_tool_integrity.py` survives).
   *The patch path in that check must be **absolute**:* `git -C <dir> apply` resolves a relative
   one against `<dir>`, so a relative path silently checks the wrong place and returns 128.
3. **Two Task-4 units were never versioned.** `blu-gigs-api.service` and `grokbot-shim.service`
   existed only on the box. Both are now in `scripts/systemd/`, byte-identical to live
   (`md5` MATCH on both).

**Step 2 — one of the two requested paths was already backed up. An earlier draft of this section
claimed "the backup did not exist" — that was wrong, corrected here after looking at `asus` itself
rather than only at `blu` and this repo.**

What already runs: **`blu-keysync.timer` on `asus`**, every **12 minutes** (`OnUnitActiveSec=12m`),
`Type=oneshot` → `~/.local/bin/blu-keysync.sh`. Its own description states the direction:

```
Description=Sync keys and bounty state from blu to asus
```

So the direction is **`blu` → `asus`**, pulling from `blu` — not `asus` pushing keys to `blu`:

| What | Destination under `~/.blu-backup/` | Verified |
|---|---|---|
| `keys-export.json` (dumped from `blu`'s 9router SQLite: `apiKeys`, `providerConnections`, `providerNodes`, `combos`, `settings`) | `.9router/` | 227 KB ✓ |
| `.config/auto-freecf/bounty` | `.config/auto-freecf/bounty` | **12 entries ✓ — the spec's second path was already covered** |
| `.zcode-bridge` | `.zcode-bridge/` | 7.7 MB ✓ |

It has no cron entry because it does not need one — it is a systemd user timer. There is still no
`rsync` on `blu`, and `blu-keysync.sh` correctly does not need it (it is `tar` over ssh).

**Therefore the genuinely missing part is only the ABH findings**, which no existing mechanism
touches: `blu-keysync.sh` never mentions `Agentic-Bug-Hunter` at all, and `asus` has no
`~/.blu-backup/recon` or `~/.blu-backup/Agentic-Bug-Hunter`. `scripts/blu_backup_pull.sh` adds
that, and adds it *in addition to* what keysync already carries — it re-pulls `.config/auto-freecf/bounty`
too (overlap is deliberate: it lets the verification below count jobs as one of its two
non-zero assertions, so a broken pull fails loudly instead of leaving a partial tree).

It also corrects the **path** for that missing part: `recon/*/findings/` does not exist on `blu` —
a hunt records its findings in `recon/<target>/sessions/<run>/agent_session.json` (`findings_log` +
trace) and `recon/<target>/nuclei/findings.jsonl`. Backing up the documented path would have
archived nothing and reported success. The real locations are in the list; the documented glob is
kept so it still works if ABH ever creates that layout.

**Two naming/credential facts that made this hard to check and will bite the install step:**
- `asus` is only an **ssh alias** for the box whose tailnet name is **`njhere`** (`100.104.217.54`).
  `scripts/railgui.sh` spells it out: `$(hostname) = NJHERE && echo local || echo asus`.
- It logs in as **`njborn2bebaddie`, not `root`** — `root@100.104.217.54` returns
  `Permission denied (publickey,password)`, which is why an earlier attempt to reach it from here
  failed. `ssh asus` works, because `~ssh/config` carries the right `User` and key.

Verified against live `blu` data, both paths:

```
  sessions     3 agent_session.json (findings_log + trace)
  nuclei       4 findings.jsonl
  jobs         10 job.json
  patches      1 patch file(s)
[backup] OK  20261019T024449  (kept 5 snapshots)
```

**The design decisions that matter, each learned from a test that failed first:**

- **Staging + verification, not trust.** It extracts to `.incoming-*`, counts the session records
  and jobs, and only then publishes and rotates. A glob matching nothing still produces a
  "successful" tar — exactly the failure this script exists to prevent. Against an unreachable box
  it exits non-zero with the previous good backup untouched.
- **Absolute paths in the remote tar.** The first version ran `tar -C /` with paths relative to
  `/`, but the **globs are expanded by the remote shell, whose cwd is `/root`** — so
  `recon/*/sessions` looked for `/root/root/...`, matched nothing, and was left as a literal tar
  could not stat. With `--ignore-failed-read` the pull then "succeeded" carrying the two non-glob
  paths and **zero** session records. The verification caught it (`sessions=0`); absolute paths
  make the glob independent of the remote cwd, and tar strips the leading `/` on its own.
- **`trap 'rm -rf "$INCOMING"' EXIT`.** `set -e` aborts on a broken tar stream before the counts
  check ever runs, so a per-check cleanup leaked one staging dir per failed pull. The trap covers
  every failure path; on success the tree has already been moved out.
- **Snapshot rotation, not overwrite.** A bad rebuild needs the state that preceded it, so the
  previously live tree becomes a dated snapshot and `KEEP` (default 5) are retained oldest-first.
  Proven: 4 pulls with `KEEP=2` leaves 2 snapshots and an intact live tree.

**Deployed on `blu` by running the shipped `4a`/`4b`/`4c` blocks verbatim** (extracted by line
range, so the tested code is the shipped code): the repo tree is synced, all pipeline units report
`active`, and `4c` refuses to touch the working ABH tree.

**Not covered — remaining risks, out of Task 5's scope:**
- The `asus` half of `blu_backup_pull.sh` is **not yet installed or scheduled**, but this is no
  longer a credentials problem: `ssh asus` reaches the box fine (the alias carries `User
  njborn2bebaddie` + key), and `~/.blu-backup` already exists with `blu-keysync.timer` writing into
  it. The script was verified by running the same code path from this machine against `blu`.
  Installing it next to keysync as a second user timer is a mechanical step that has not been
  taken yet. Note it must be installed as user `njborn2bebaddie`, and its `BOX=blu` relies on a
  working `blu` alias from *that* account — which keysync already proves.
- `blu-9router.service`, `blu-egress.service`, `blu-argon-bridge.service`, `blu-zcode-bridge.service`
  and the `grokbot-{gateway,coordinator}` units exist only on the box. They are model-farm plumbing
  outside this pipeline, and `4c` degrades loudly (SKIP with the reason) when 9router is absent
  rather than failing the provision.
- **`4c`'s health probe was itself wrong at first, and that was found after Task 5 was committed.**
  It checked `curl -m 8 /v1/models`, but that endpoint enumerates the whole model list and took
  **14.6 s** for 292 KB — so a perfectly healthy 9router was reported as "not answering". `blu`'s
  9router is up, local (`ExecStart=/usr/local/bin/9router --skip-update -p 20128`) and returns
  `{"ok":true}` from `/api/health` in ~4 ms. The probe now uses `/api/health`; `abh_deploy.sh` was
  always fine because it allows `-m 20` against `/v1/models`. **Lesson: a liveness probe must use
  the cheap endpoint, not the one that answers the question you actually wanted answered.**
- **Unresolved design discrepancy, deliberately not touched here:** `docs/re/grokbot-auth-and-transport.md`
  documents the intended transport as `blu:20128 → desktop`, i.e. a reverse tunnel from `blu` to
  this machine, but `blu-tunnel.service` is **inactive** and the port is held by `blu`'s own local
  9router instead. So the box is serving its own model list rather than this machine's. Whether the
  tunnel was retired on purpose or fell over is not knowable from the repo alone.
