#!/usr/bin/env bash
# Deploy Agentic-Bug-Hunter (ABH) onto blu and point its brain at the local
# 9router.
#
# WHY THIS SCRIPT EXISTS
# ----------------------
# blu is ephemeral: a Manus rebuild gives a fresh box with a new hostname, so
# every hand-applied edit is lost. ABH is the actual hunt engine now (9-phase
# recon, deterministic ScopeChecker, 7-Question Gate, ReAct loop), and it needs
# three patches to run here at all:
#
#   1. brain.py  — 9router answers a non-streaming request with
#                  `content-type: text/event-stream` and appends `data: [DONE]`
#                  to the JSON body. `r.json()` then raises "Extra data", the
#                  error is swallowed by a broad except, and every caller sees
#                  an empty string. Symptom: the 7-Question Gate prints
#                  `DECISION: UNKNOWN` even though the model answered correctly.
#   2. agent.py  — the ReAct loop was written against the Ollama Python client
#                  and refuses to start without it. There is no local Ollama
#                  here; every model lives behind 9router. Adds an
#                  OpenAI-compatible client and fixes a run_recon() call that
#                  passed a `max_urls` argument the function does not accept
#                  (every recon step died with TypeError, so the hunt reported
#                  "Steps executed: 0").
#   3. recon_engine.sh — two network calls had no timeout (curl to
#                  web.archive.org, `nmap -sV --top-ports 1000`). Both hung for
#                  5+ minutes on a CDN-fronted target and the hunt never
#                  reached the vulnerability sweep.
#
# Run this on blu as root:   bash abh_deploy.sh
# Re-runnable: it fetches the upstream tree once, then re-applies the patch.
set -euo pipefail

ABH_DIR=${ABH_DIR:-/root/Agentic-Bug-Hunter}
REPO=${REPO:-https://github.com/awarexone/Agentic-Bug-Hunter.git}
PATCH=${PATCH:-/root/Auto-FreeCF/patches/agentic-bug-hunter-blu.patch}
ENVFILE=${ENVFILE:-/root/abh.env}
ROUTER_KEY=${ROUTER_KEY:-/root/grokbot/router.key}
# Must be a model that actually answers RIGHT NOW, because it is the first
# attempt of every call. See the FALLBACKS note below.
MODEL=${MODEL:-ag/gemini-3.8-flash-high}

say() { printf '\n\033[1;36m[abh]\033[0m %s\n' "$*"; }
die() { printf '\n\033[1;31m[abh] FATAL:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "run as root (system units + /root paths)"

# ── 1. source tree ────────────────────────────────────────────────────────────
# cvelistV5 is a 7 GB nested clone of the CVE corpus. Nothing in the code
# references it, and pulling it here would dominate the transfer, so it is
# skipped and the rest of the tree is fetched shallow.
if [ -d "$ABH_DIR/.git" ]; then
  say "updating existing checkout at $ABH_DIR"
  # Upstream rewrote history on 2026-10-21 (our base commit 93aa1fe no longer
  # exists), so a shallow fetch can leave origin/HEAD pointing at a commit the
  # new history cannot reach. Fetching the branch by name and resetting to
  # FETCH_HEAD is what actually lands on current upstream.
  git -C "$ABH_DIR" fetch --depth 1 origin main >/dev/null 2>&1 \
    || git -C "$ABH_DIR" fetch --depth 1 origin master >/dev/null 2>&1 || true
  git -C "$ABH_DIR" reset --hard FETCH_HEAD >/dev/null 2>&1 \
    || git -C "$ABH_DIR" reset --hard origin/HEAD >/dev/null 2>&1 || true
else
  say "cloning $REPO -> $ABH_DIR (skipping cvelistV5)"
  # recon/ is GITIGNORED, so `git clone` into a fresh dir is the only way to
  # lose it — and it is the one thing on this box that cannot be rebuilt: every
  # session trace, every nuclei result, every finding. blu's tree is a scp'd
  # copy with no .git, so this branch IS the normal path after a rebuild, not an
  # edge case. Preserve the artifacts across the swap.
  KEEP=$(mktemp -d)
  if [ -d "$ABH_DIR/bughunter/recon" ]; then
    say "preserving $(du -sh "$ABH_DIR/bughunter/recon" 2>/dev/null | cut -f1) of recon artifacts"
    cp -a "$ABH_DIR/bughunter/recon" "$KEEP/recon" || die "could not preserve recon/"
  fi
  rm -rf "$ABH_DIR"
  git clone --depth 1 "$REPO" "$ABH_DIR" >/dev/null 2>&1 \
    || die "clone failed (is the repo still public?)"
  if [ -d "$KEEP/recon" ]; then
    mkdir -p "$ABH_DIR/bughunter"
    cp -a "$KEEP/recon/." "$ABH_DIR/bughunter/recon/"
    say "restored recon artifacts ($(find "$ABH_DIR/bughunter/recon" -name 'agent_session.json' | wc -l) sessions)"
  fi
  rm -rf "$KEEP"
fi

# ── 2. patches ────────────────────────────────────────────────────────────────
[ -f "$PATCH" ] || die "patch not found: $PATCH (copy patches/agentic-bug-hunter-blu.patch to blu first)"
if git -C "$ABH_DIR" apply --check "$PATCH" 2>/dev/null; then
  git -C "$ABH_DIR" apply "$PATCH"
  say "applied $(basename "$PATCH")"
elif git -C "$ABH_DIR" apply --reverse --check "$PATCH" 2>/dev/null; then
  say "patch already applied — skipping"
else
  die "patch does not apply and is not already applied; upstream changed. Re-derive it."
fi

# ── 3. recon toolchain ────────────────────────────────────────────────────────
# blu already has these under /root/go/bin from blu_bootstrap.sh, but a fresh
# box may not. Only install what is missing; `go install` is slow.
export PATH=/root/go/bin:/root/.local/bin:$PATH
if command -v go >/dev/null 2>&1; then
  for t in subfinder httpx nuclei katana dnsx gau anew qsreplace waybackurls dalfox interactsh-client; do
    if ! command -v "$t" >/dev/null 2>&1; then
      say "installing $t"
      go install "github.com/projectdiscovery/$t/cmd/$t@latest" >/dev/null 2>&1 \
        || printf '  (could not install %s — recon degrades gracefully)\n' "$t"
    fi
  done
else
  printf '[abh] go not installed — skipping toolchain (recon will be limited)\n'
fi

# ── 4. brain wiring ───────────────────────────────────────────────────────────
[ -f "$ROUTER_KEY" ] || die "9router key missing: $ROUTER_KEY"
KEY=$(cat "$ROUTER_KEY")
[ -n "$KEY" ] || die "9router key file is empty"

# 9router must actually be serving before we point a 2-hour hunt at it.
#
# Probe /api/health, NOT /v1/models. Measured on blu 2026-10-21: /api/health
# answers in 8ms; /v1/models takes 18.4s and returns 292KB (666 models), so a
# 20s timeout sits right on the edge and intermittently returns 000. A health
# check that fails on a HEALTHY router is worse than no check: it aborts a
# deploy that would have worked, and it teaches you to ignore the one message
# that means "the model gateway is down".
CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 10 \
  http://127.0.0.1:20128/api/health || true)
if [ "$CODE" != "200" ]; then
  # Fall back to the models endpoint only to distinguish "router down" from
  # "router up but slow": a 000 here after a 200 above means auth/upstream.
  CODE2=$(curl -s -o /dev/null -w '%{http_code}' -m 45 \
    -H "Authorization: Bearer $KEY" http://127.0.0.1:20128/v1/models || true)
  [ "$CODE2" = "200" ] || die "9router not healthy on :20128 (health=$CODE models=$CODE2); start blu-9router"
fi

# Model chain. Every entry must be PROBED live on :20128 before it lands here —
# a dead model in the middle of the chain silently costs 65s per call and a
# model at the END of it defeats failover entirely.
#   verified live 2026-10-19 (scripts/r9_modelcheck.py):
#     Zcode/GLM-5.3, ag/gemini-3.8-flash-high, THK/deepseek-v4.1-flash:free,
#     nr/step-5-preview, ocz/mimo-v2.6-flash-free, ocz/nemotron-3-ultra-free
#   verified DEAD: cbai/deepseek-v4.1-flash (upstream 502 on all 3
#     codebuddy-intl accounts) — deliberately absent from the chain.
#   Also dead as of 2026-10-19, and removed: ocz/space-bunny-free (upstream 410
#     Gone). It used to be the LAST hop, so every failover ended on a model that
#     could not answer — the exact failure the paragraph above warns about.
# Re-verify after any upstream change: python3 scripts/r9_modelcheck.py --models <list>
# Read by LLMClient.chat() (brain.py) and OpenAICompatClient.chat() (agent.py);
# both walk the chain on empty content, ReadTimeout, or HTTP error.
# ORDER MATTERS AND WAS WRONG. Measured on the laptop 2026-10-21 20:20 WIB
# against the same 9router build: Zcode/GLM-5.3 -> HTTP 503 after 12.4s,
# ag/gemini-3.8-flash-high -> 3.1s, THK/deepseek-v4.1-flash:free -> timeout at
# 60s. The GLM-5.3 failure is quota, not config: all 10 accounts holding a
# model:glm-5.3 bucket sit at 3,000,000/3,000,000 units. Leading the chain with
# a model that cannot answer costs every call 12s before failover, and a hunt
# makes dozens of calls. Put the model that answers first; keep GLM-5.3 second
# so it is used again the moment the farm restores its quota.
FALLBACKS=${FALLBACKS:-"ag/gemini-3.8-flash-high,Zcode/GLM-5.3,ocz/mimo-v2.6-flash-free,nr/step-5-preview,THK/deepseek-v4.1-flash:free"}

# Cross-check the chain against the last model-health sweep (r9_modelcheck.py).
# Not fatal: the snapshot can be stale and a deploy must not be blocked by a
# probe. But it is how ocz/space-bunny-free would have been caught while it sat
# at the END of the chain returning 410 -- every failover landed on a model that
# could not answer, and nothing said so.
HEALTH="${HOME:-/root}/.config/auto-freecf/r9_model_health.json"
if [ -f "$HEALTH" ]; then
  python3 - "$HEALTH" "$FALLBACKS" <<'PY' || true
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
res = d.get("results", {})
bad = [m.strip() for m in sys.argv[2].split(",")
       if m.strip() and res.get(m.strip(), {}).get("ok") is False]
if bad:
    print("  ! failover chain has models that FAILED the last health sweep"
          f" ({d.get('checked_at','?')}):")
    for m in bad:
        print(f"      - {m}: {res[m].get('error','')[:90]}")
    print("      re-check: kancahub 9router models check --models '" + ",".join(bad) + "'")
PY
fi

umask 077
cat > "$ENVFILE" <<EOF
# ABH runtime environment on blu. Generated by scripts/abh_deploy.sh — do not
# hand-edit; re-run the deploy script instead.
export PATH=/root/go/bin:/root/.local/bin:\$PATH
export OPENAI_API_KEY="$KEY"
export OPENAI_BASE_URL="http://127.0.0.1:20128/v1"
export BRAIN_PROVIDER=openai
export BRAIN_MODEL="$MODEL"
# Ordered failover chain for BOTH brain.py and agent.py. Probed live before
# writing; see the comment above in scripts/abh_deploy.sh.
export BOUNTY_MODEL_FALLBACKS="$FALLBACKS"
EOF
chmod 600 "$ENVFILE"
say "wrote $ENVFILE (model=$MODEL)"

# ── 5. prove the brain answers ────────────────────────────────────────────────
# A silent brain is the failure mode this whole script exists to prevent, so
# verify a real completion instead of just checking that a key file exists.
say "verifying brain round-trip through 9router"
if ! ( source "$ENVFILE"; cd "$ABH_DIR" && python3 - <<'PY'
import sys
sys.path.insert(0, "bughunter")
from brain import LLMClient
import os
c = LLMClient("openai", model=os.environ["BRAIN_MODEL"])
out = c.chat(os.environ["BRAIN_MODEL"], "You are terse.",
             "Reply with exactly: OK", max_tokens=20)
if not (out or "").strip():
    sys.exit("brain returned empty content")
print("brain says:", out.strip()[:60])
PY
); then
  die "brain round-trip failed — check blu-9router and the model id"
fi

# ── 6. CVE intel inputs ──────────────────────────────────────────────────────
# run_cve_intel is the step that turns "nginx 1.18.0" into a testable shortlist.
# Without the databases it returns an explicit "not found" message, and the hunt
# proceeds blind — a missing input that looks like "no candidates" in the digest.
CVEDIR=${HOME:-/root}/.config/auto-freecf
for db in cve.db cve_research.db; do
  if [ -f "$CVEDIR/$db" ]; then
    say "cve intel: $db present ($(du -h "$CVEDIR/$db" | cut -f1))"
  else
    printf '\033[1;33m[abh] cve intel: %s MISSING\033[0m — run_cve_intel will report "not found".\n' "$db"
    printf '  Pull it from the laptop backup, or rebuild with scripts/build_cve_index.py.\n'
  fi
done

say "done. Run a hunt with:"
printf '  source %s && cd %s && python3 bughunter/agent.py --target HOST -d HOST\n' "$ENVFILE" "$ABH_DIR"
