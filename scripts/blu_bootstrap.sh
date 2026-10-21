#!/bin/sh
# Provision a fresh Cue/Manus sandbox (or any Ubuntu box) as an opencode worker.
# Runs ON THE LAPTOP and drives the box over ssh. Idempotent — safe to re-run.
#
#   scripts/blu_bootstrap.sh                 # target the box aliased "blu"
#   scripts/blu_bootstrap.sh                 # + pentest tooling:  TOOLS=1
#   scripts/blu_bootstrap.sh                 # + Drost security MCP: DROST=1
#   BOX=other scripts/blu_bootstrap.sh       # any ~/.ssh/config alias
#
# What lands on the box: 2 skill trees (~/.config/opencode/skills = general,
# ~/.claude/skills = the pentest/gig pack), AGENTS.md, opencode + uv, a 3-model
# 9Router config (rea MCP included), a 45 s idle heartbeat, and a reverse tunnel
# back to the laptop's router.
#
# What it does NOT do: copy ~/.config/opencode/opencode.json from the laptop.
# That file is 27 KB of provider keys (9Router 153 connections, THK, ...) and
# AGENTS.md's hard rule is secrets stay off throwaway boxes. Models reach the
# box through a REVERSE ssh tunnel instead, so the laptop's 127.0.0.1 bind stays
# as it is. Only ONE scoped router key lands on the box, in a 0600 config.
set -eu
BOX=${BOX:-blu}
SRC=${SRC:-$HOME/.config/opencode}
# Second skill source. opencode MERGES ~/.config/opencode/skills with
# ~/.claude/skills, and the pentest/gig pack (bug-bounty, hunt-*, autopilot,
# sast, web3-audit, rea...) lives ONLY in the second one. Shipping just SRC
# gives a box with 72 skills and zero hunting ability. Shipped to the same
# ~/.claude/skills path on the box so opencode's merge finds it there.
SKILL2=${SKILL2:-$HOME/.claude/skills}
# TOOLS=1 installs the pentest toolchain the skills actually call (nmap, ffuf,
# nuclei, subfinder, httpx, semgrep, sqlmap). Off by default: it is a big apt
# layer on a reset-able box, and several skills degrade gracefully without it.
TOOLS=${TOOLS:-0}
# DROST=1 provisions Drost Community Edition, replacing the old HexStrike AI MCP
# (flag HEXSTRIKE, code removed). Same job — an offensive-security MCP toolkit —
# better in every way that mattered here:
#
#   * HexStrike discovered its tools by scanning PATH, so a unit without
#     /root/go/bin silently reported 28/127 tools and ran a degraded hunt while
#     reporting success. That is the worst failure mode for bounty work: you
#     believe you scanned and you did not. Drost's catalog is executable-backed
#     and self-describing (drost_catalog), so it cannot under-report that way.
#   * HexStrike needed ~198 external tools on the host, which is exactly why it
#     was off by default and only worked with TOOLS=1 on top. Drost ships the
#     whole toolchain INSIDE one image, so DROST=1 no longer implies TOOLS=1.
#   * HexStrike ran an unauthenticated REST server on 127.0.0.1:8888. Drost
#     transports over stdio and publishes no ports at all.
#   * Drost's image is signed with Sigstore provenance + SPDX/CycloneDX SBOM
#     attestations; HexStrike was a bare git clone with no provenance.
#
# NOT default: the Kali image is multi-GB, so a blu rebuild re-pulls it.
DROST=${DROST:-0}
CFG=${CFG:-$HOME/.config/opencode/opencode.json}
MODEL=${MODEL:-THK/deepseek-v4.1-flash:free}

say() { printf '  %-9s %s\n' "$1" "$2"; }

# 1. capability layer — agent rules + skills, no credentials.
#    tar, not `scp -r`: scp nests the dir (skills/skills) and drops files.
ssh -o BatchMode=yes "$BOX" 'mkdir -p ~/.config/opencode'
tar -C "$SRC" -cf - AGENTS.md | ssh -o BatchMode=yes "$BOX" 'tar -C ~/.config/opencode -xf -'
tar -C "$SRC" -cf - skills | ssh -o BatchMode=yes "$BOX" 'rm -rf ~/.config/opencode/skills && tar -C ~/.config/opencode -xf -'
say payload "$(ssh -o BatchMode=yes "$BOX" 'ls ~/.config/opencode/skills | wc -l') skills + AGENTS.md"

# 1b. second skill source — the pentest/gig pack. Merged by opencode at load
#     time from ~/.claude/skills, so it MUST land at that path, not under
#     .config/opencode. tar again (scp -r nests and drops files).
if [ -d "$SKILL2" ]; then
  ssh -o BatchMode=yes "$BOX" 'mkdir -p ~/.claude'
  tar -C "$(dirname "$SKILL2")" -cf - "$(basename "$SKILL2")" | ssh -o BatchMode=yes "$BOX" 'rm -rf ~/.claude/skills && tar -C ~/.claude -xf -'
  say skills2 "$(ssh -o BatchMode=yes "$BOX" 'ls ~/.claude/skills | wc -l') skills (pentest pack) -> ~/.claude/skills"
else
  say skills2 "SKIP ($SKILL2 not found)"
fi

# 2. box-side tooling. ~/.opencode/bin and ~/.local/bin are not on a non-login
#    ssh PATH, so export before probing. The opencode installer is bash-only
#    (set -euo pipefail) and this box's sh is dash, hence the explicit bash.
ssh -o BatchMode=yes "$BOX" 'sh -s' <<'REMOTE'
set -eu
export PATH="$HOME/.local/bin:$HOME/.opencode/bin:$PATH"
command -v uv >/dev/null 2>&1 || curl -fsSL https://astral.sh/uv/install.sh | sh >/dev/null 2>&1
command -v opencode >/dev/null 2>&1 || { curl -fsSL https://opencode.ai/install -o /tmp/oc.sh && bash /tmp/oc.sh; } >/dev/null 2>&1
rm -f /tmp/oc.sh
printf '  %-9s %s\n' opencode "$(command -v opencode || echo MISSING)"
printf '  %-9s %s\n' uv "$(command -v uv || echo MISSING)"
REMOTE

# 2b. optional pentest toolchain. The hunt skills call these binaries; without
#     them they still reason but cannot run a scan. apt is slow and adds
#     surface, so it is opt-in (TOOLS=1).
#
# DROST deliberately does NOT imply this layer: Drost runs its own 107 tools
# inside the container, so installing host binaries for it was pure duplication
# (that is the coupling that made HEXSTRIKE=1 force TOOLS=1 before). Tool calls
# go to drost_* MCP tools and never touch the host PATH.
if [ "$TOOLS" = "1" ]; then
  ssh -o BatchMode=yes "$BOX" 'sh -s' <<'REMOTE'
set -u
export DEBIAN_FRONTEND=noninteractive
# cargo's bin dir is NOT ~/.cargo/bin on this image: CARGO_HOME resolves to
# /usr/local/cargo, so `cargo install` drops binaries in /usr/local/cargo/bin.
# Measured 2026-10-15 — probing ~/.cargo/bin made rustscan+feroxbuster look
# "missing" when both had compiled fine. Keep both paths.
export PATH="/usr/local/cargo/bin:$HOME/.cargo/bin:$HOME/go/bin:$HOME/.local/bin:$HOME/.local/share/uv/tools/bin:$PATH"
sudo -n apt-get update -qq >/dev/null 2>&1 || true
# ONE package per apt call. `apt-get install a b c` aborts the WHOLE batch on a
# single unknown name, which would silently leave the box bare. The loop cannot.
# Measured 2026-10-15 on Ubuntu 24.04: of the 27 names tried, these 21 install.
# The rest do NOT exist as apt packages (checked one by one, not guessed):
#   rustscan feroxbuster autorecon responder crackmapexec evil-winrm enum4linux-ng
#   theharvester(only via uv) volatility3(uv) --content-disposition
ok() { command -v "$1" >/dev/null 2>&1; }
for p in nmap masscan ffuf gobuster dirb nikto sqlmap hydra john hashcat gdb \
         radare2 binwalk exiftool steghide foremost dnsutils whois whatweb \
         sslscan patator medusa amass golang-go ripgrep jq man-db \
         fierce dnsenum arp-scan nbtscan smbclient smbmap wfuzz commix \
         ophcrack outguess scalpel bulk-extractor sleuthkit binutils awscli \
         recon-ng zaproxy python3-pip pipx cargo; do
  sudo -n apt-get install -y -qq --no-install-recommends "$p" >/dev/null 2>&1 || true
done
printf '  %-9s %s\n' apt "$(for t in nmap ffuf sqlmap hydra john hashcat gdb radare2 binwalk wfuzz smbmap fls; do ok $t && printf '%s ' $t; done)"
# Rust tools. Cargo IS present on the image (1.97.1) and crates.io is reachable;
# these two simply take a long time (~400 crates for feroxbuster), so the probe's
# 900 s budget was the only real blocker. Budget 30 min and reuse the registry
# cache on re-runs, which makes the second pass minutes instead of tens of them.
if ok cargo; then
  for c in rustscan feroxbuster; do
    timeout 1800 cargo install --quiet "$c" >/dev/null 2>&1 || true
  done
  printf '  %-9s %s\n' cargo "$(for t in rustscan feroxbuster; do ok $t && printf '%s ' $t; done)"
else
  printf '  %-9s %s\n' cargo "MISSING (rustscan/feroxbuster skipped)"
fi
# Go tools: apt ships these stale or not at all, so they come from source.
# Needs golang-go, installed by the apt loop above.
if ok go; then
  for m in github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest \
           github.com/projectdiscovery/httpx/cmd/httpx@latest \
           github.com/projectdiscovery/subfinder/v2/cmd/subfinder@latest \
           github.com/projectdiscovery/katana/cmd/katana@latest \
           github.com/hahwul/dalfox/v2@latest \
           github.com/projectdiscovery/hakrawler/cmd/hakrawler@latest \
           github.com/lc/gau/v2/cmd/gau@latest \
           github.com/tomnomnom/waybackurls@latest \
           github.com/tomnomnom/anew@latest \
           github.com/tomnomnom/qsreplace@latest \
           github.com/haccer/subjack@latest \
           github.com/trufflesecurity/trufflehog/v3@latest; do
    timeout 300 go install "$m" >/dev/null 2>&1 || true
  done
  printf '  %-9s %s\n' go "$(for t in nuclei httpx subfinder katana dalfox gau waybackurls anew subjack trufflehog; do ok $t && printf '%s ' $t; done)"
else
  printf '  %-9s %s\n' go "MISSING (nuclei/httpx/subfinder/katana/dalfox/gau/... skipped)"
fi
# Python tools. Debian 24.04's system pip refuses to touch its own dist-packages
# ("Cannot uninstall packaging 24.0, RECORD file not found"), so these go through
# uv in an isolated per-tool venv instead — uv is installed in step 2, and the
# binaries still land in ~/.local/bin which is already on PATH.
# Measured: netexec/scoutsuite/kube-hunter resolve but expose no binary of the
# expected name; scoutsuite installs as `scout`, volatility3 as `vol`.
for m in wafw00f dirsearch checkov arjun sslyze paramspider jwt-tool \
         prowler sherlock uro volatility3 scoutsuite theharvester; do
  timeout 300 uv tool install -q "$m" >/dev/null 2>&1 || true
done
printf '  %-9s %s\n' uv "$(for t in dirsearch wafw00f checkov arjun sslyze prowler sherlock uro vol scout; do ok $t && printf '%s ' $t; done)"
# SAST scanner: opengrep static binary, NOT semgrep from pip. Measured
# 2026-10-16 on Fedora 45: semgrep's manylinux core segfaults (-11) on any
# real scan while --help works; opengrep manylinux scans fine and reads the
# same rule registry (`--config auto`, `r/...`). Uniform on laptop + blu.
if ! ok opengrep; then
  curl -fsSL -m 280 -o "$HOME/.local/bin/opengrep" \
    https://github.com/opengrep/opengrep/releases/download/v1.31.0/opengrep_manylinux_x86 \
    >/dev/null 2>&1 && chmod +x "$HOME/.local/bin/opengrep" || true
fi
printf '  %-9s %s\n' opengrep "$(ok opengrep && opengrep --version 2>/dev/null | head -1 || echo MISSING)"
REMOTE
else
  say tools "SKIP (TOOLS=0; set TOOLS=1 for nmap/ffuf/nuclei/semgrep/sqlmap)"
fi

# 2c. optional Drost Community Edition MCP. One signed container; the MCP server
#     and its 107 executable-backed security tools share the image.
#
#     Why a container at all: the host-toolchain approach this replaces needed
#     ~198 packages on the box, and any gap there degraded the hunt silently.
#     The container ships them together, versioned, with provenance.
#
#     Idempotent: an existing correct container is left alone, and `docker pull`
#     only refreshes when the tag is missing or the run was explicitly forced.
if [ "$DROST" = "1" ]; then
  DROST_IMAGE=${DROST_IMAGE:-ghcr.io/drost-ai/drost-community:1.0.0}
  DROST_CONTAINER=${DROST_CONTAINER:-drost-ai}
  DROST_VOLUME=${DROST_VOLUME:-drost-ai-workspace}
  ssh -o BatchMode=yes "$BOX" "DROST_IMAGE='$DROST_IMAGE' DROST_CONTAINER='$DROST_CONTAINER' DROST_VOLUME='$DROST_VOLUME' FORCE='${DROST_FORCE:-0}' sh -s" <<'REMOTE'
set -u
command -v docker >/dev/null 2>&1 || {
  printf '  %-9s %s\n' drost "SKIP (docker absent)"
  exit 0
}
docker info >/dev/null 2>&1 || {
  printf '  %-9s %s\n' drost "SKIP (docker daemon not reachable)"
  exit 0
}

have_container() {
  [ "$(docker inspect -f '{{.State.Running}}' "$DROST_CONTAINER" 2>/dev/null)" = "true" ]
}

# Reuse only if it is already running AND the image tag matches what we asked
# for. A running-but-stale container is the exact silent-degradation trap this
# replaces, so "running" alone is not good enough.
image_matches() {
  docker inspect -f '{{.Config.Image}}' "$DROST_CONTAINER" 2>/dev/null | grep -qx "$DROST_IMAGE"
}

if have_container && image_matches && [ "$FORCE" != "1" ]; then
  printf '  %-9s %s\n' drost "upstream image already current"
else
  # Pull BEFORE deleting: if the pull fails (bad tag, flaky registry) we keep
  # the working container rather than tearing it down for nothing.
  printf '  %-9s %s\n' pull "docker pull $DROST_IMAGE"
  if docker pull "$DROST_IMAGE" >/tmp/drost-pull.log 2>&1; then
    if have_container || docker inspect "$DROST_CONTAINER" >/dev/null 2>&1; then
      docker rm -f "$DROST_CONTAINER" >/dev/null 2>&1 || true
    fi
  else
    printf '  %-9s %s\n' drost "pull FAILED — keeping existing container"
    tail -3 /tmp/drost-pull.log | sed 's/^/              /'
    exit 0
  fi
fi

if ! have_container; then
  # --restart unless-stopped so the container survives a box hiccup, and the
  # workspace volume holds engagement artifacts across recreates. No -p: the
  # server publishes no ports by design, so there is nothing to expose.
  docker run -d --name "$DROST_CONTAINER" --restart unless-stopped \
    -v "$DROST_VOLUME:/workspace" "$DROST_IMAGE" >/dev/null 2>&1 || {
    printf '  %-9s %s\n' drost "docker run FAILED"
      exit 0
  }
fi

# --self-test is the only proof that matters: it initializes MCP, advertises the
# catalog and makes a representative call. A container that merely starts says
# nothing about whether the server inside it works.
sleep 3
if docker exec "$DROST_CONTAINER" drost-mcp --self-test >/tmp/drost-selftest.log 2>&1; then
  # Report the counts that actually matter, read from the self-test JSON: total
  # MCP tools, how many external executables are present, and whether any are
  # MISSING. A non-empty missing_external_tools means a degraded hunt that still
  # reports status=healthy — the exact HexStrike failure mode this replaces — so
  # surface it instead of printing only the headline number.
  #
  # Kept as grep -oE rather than awk field-splitting: the array value is the one
  # that matters and a naive split on quotes silently drops `"...": []`.
  SELFTEST=$(docker exec "$DROST_CONTAINER" drost-mcp --self-test 2>/dev/null)
  D_TOT=$(printf '%s' "$SELFTEST" | grep -oE '"total_mcp_tools": *[0-9]+' | grep -oE '[0-9]+' | head -1)
  D_AVL=$(printf '%s' "$SELFTEST" | grep -oE '"available_external_tools": *[0-9]+' | grep -oE '[0-9]+' | head -1)
  if printf '%s' "$SELFTEST" | grep -q '"missing_external_tools": *\[\]'; then
    D_MISS="0 (complete)"
  else
    D_MISS="PRESENT -> $(printf '%s' "$SELFTEST" | grep -oE '"missing_external_tools": *\[[^]]*\]' | head -1)"
  fi
  printf '  %-9s %s\n' drost "self-test OK"
  printf '  %-9s %s\n' drost-mcp "${D_TOT:-?} MCP tools, ${D_AVL:-?} external, missing: $D_MISS"
else
  printf '  %-9s %s\n' drost "self-test FAILED"
  tail -5 /tmp/drost-selftest.log | sed 's/^/              /'
  exit 0
fi

printf '  %-9s %s\n' drost-id "$(docker inspect -f '{{.State.Status}}' "$DROST_CONTAINER" 2>/dev/null)"
printf '  %-9s %s\n' drost-vol "$DROST_VOLUME"
REMOTE
else
  say drost "SKIP (DROST=0; its 107 tools live in the container, no host apt needed)"
fi

# 3. one scoped router key + a small config. Deliberately not the laptop's
#    25 KB block: 150 models is not needed to run a job, and /v1/models does not
#    validate the key anyway — chat does, so a truncated key fails late.
#    Model list = the same verified free chain zmine uses, minus Zcode (whose
#    shared exit must not take farm/bounty traffic).
KEY=$(python3 -c "import json;print(json.load(open('$CFG'))['provider']['9router']['options']['apiKey'])")
# The MCP block is conditional on DROST: opencode fails to start a configured
# server whose command does not exist, so a "drost" entry on a box without the
# container produces a dead server on every boot. Build the block from the same
# flag that provisions it.
if [ "$DROST" = "1" ]; then
  DROST_MCP='    "drost": {
      "type": "local",
      "command": ["docker", "exec", "-i", "'"$DROST_CONTAINER"'", "drost-mcp"],
      "enabled": true
    }'
else
  DROST_MCP=''
fi
ssh -o BatchMode=yes "$BOX" "cat > ~/.config/opencode/opencode.json && chmod 600 ~/.config/opencode/opencode.json" <<JSON
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "9router": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "9Router (via laptop tunnel)",
      "options": { "baseURL": "http://127.0.0.1:20128/v1", "apiKey": "$KEY" },
      "models": {
        "$MODEL": { "name": "scoped" },
        "nr/step-5-preview": { "name": "free" },
        "kilo/poolside/laguna-s-2.1:free": { "name": "free" }
      }
    }
  },
  "mcp": {
    "rea": {
      "type": "local",
      "command": ["npx", "-y", "rea-agents@6.3.0", "mcp"],
      "enabled": true
    }$( [ -n "$DROST_MCP" ] && printf ',\n%s' "$DROST_MCP" )
  },
  "model": "9router/$MODEL"
}
JSON
say config "$(ssh -o BatchMode=yes "$BOX" 'stat -c%s ~/.config/opencode/opencode.json') bytes, mode 600"

# 3b. idle keep-alive ON THE BOX. Manus sandboxes pause after ~30 min idle; a
#     tiny heartbeat (log + CPU spin + tunnel touch every 45 s) keeps the box
#     awake indefinitely. Near-zero cost, no z.ai traffic, installed every
#     provision so a fresh box never sleeps on us.
ssh -o BatchMode=yes "$BOX" 'mkdir -p ~/.config/systemd/user'
scp -o BatchMode=yes "$(dirname "$0")/systemd/blu-idle.service" "$BOX:.config/systemd/user/blu-idle.service"
ssh -o BatchMode=yes "$BOX" 'systemctl --user daemon-reload && systemctl --user enable --now blu-idle.service && sleep 2 && systemctl --user is-active blu-idle.service'
say idle "$(ssh -o BatchMode=yes "$BOX" 'systemctl --user is-active blu-idle.service')"

# 4. reverse tunnel, laptop side. Same trick as zcode-asus-tunnel: the laptop
#    dials OUT, so nothing listens on the box when the laptop is asleep.
cp "$(dirname "$0")/systemd/blu-tunnel.service" ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now blu-tunnel.service
sleep 5
say tunnel "$(systemctl --user is-active blu-tunnel.service)"

# 4a. the bounty pipeline's own code. A fresh Manus box has no /root/Auto-FreeCF
#     at all: nothing here clones it, and blu's copy is a scp'd tree rather than
#     a git repo. So the ABH patch, gigs_api.py, the unit files installed in 4b
#     and the test suite all fail to exist after a rebuild — which is why a
#     rebuilt box could not run the pipeline even though every unit was fixed.
#     Only the pipeline subtrees cross: the repo root is a 1.5 GB farm and
#     scraper tree with its own credentials, and a throwaway box must not
#     receive those (the same rule that keeps the laptop's opencode.json off).
D=$(dirname "$0")
PIPE="scripts patches tests grokbot-shim"
tar -C "$D/.." --exclude='__pycache__' --exclude='.git' -cf - $PIPE \
  | ssh -o BatchMode=yes "$BOX" \
      'mkdir -p /root/Auto-FreeCF && rm -rf /root/Auto-FreeCF/scripts/__pycache__ && tar -C /root/Auto-FreeCF -xf -'
say code "$(ssh -o BatchMode=yes "$BOX" 'du -sh /root/Auto-FreeCF 2>/dev/null | cut -f1') ($PIPE)"

# 4b. the pipeline's units. blu-bounty is the 24/7 loop (a long-running service
#     now, not a timer tick); blu-model-health probes the model chain every 30
#     minutes; blu-r9-farm refills quota; blu-gigs-api is the control API and
#     grokbot-shim the chat surface that drives it. All of them existed only on
#     the box, so a rebuild came back with a pipeline nobody could steer.
#     Installed from the repo copies on every provision, so the repo and the box
#     cannot drift apart again.
#
# blu-bounty.timer is gone on purpose: it fired the hunt every 3h with up to 40
# min of randomized delay, which left the box idle ~20h a day and capped each
# program at 0.5h. The service now runs continuously and takes its own lock, so
# a leftover timer invocation exits quietly instead of racing the loop.
SYS=/etc/systemd/system
scp -o BatchMode=yes "$D/systemd/blu-bounty.service" \
                     "$D/systemd/blu-model-health.service" "$D/systemd/blu-model-health.timer" \
                     "$D/systemd/blu-r9-farm.service" "$D/systemd/blu-r9-farm.timer" \
                     "$D/systemd/blu-gigs-api.service" "$BOX:$SYS/"
# blu-bounty.timer is disabled, not merely un-enabled: a box provisioned by an
# older run of this script still has it, and an enabled timer on a Type=simple
# service starts a second copy every 3h. The lock makes that harmless, but the
# journal fills with "another bounty loop holds ..." noise.
ssh -o BatchMode=yes "$BOX" 'systemctl disable --now blu-bounty.timer 2>/dev/null; rm -f /etc/systemd/system/blu-bounty.timer; systemctl daemon-reload && systemctl enable --now blu-bounty.service blu-model-health.timer blu-r9-farm.timer blu-gigs-api.service'
say bounty "$(ssh -o BatchMode=yes "$BOX" 'systemctl is-active blu-bounty.service blu-model-health.timer blu-r9-farm.timer blu-gigs-api.service' | tr '\n' ' ')"

# The shim's ExecStart points into /root/grokbot (proxy.js + router.key), which
# is NOT part of this repo, so its unit is installed only when that tree is
# already present. Enabling it otherwise starts a unit that can only fail-loop.
if ssh -o BatchMode=yes "$BOX" 'test -f /root/grokbot/9router-shim/proxy.js'; then
  scp -o BatchMode=yes "$D/systemd/grokbot-shim.service" "$BOX:$SYS/"
  ssh -o BatchMode=yes "$BOX" 'systemctl daemon-reload && systemctl enable --now grokbot-shim.service'
  say shim "$(ssh -o BatchMode=yes "$BOX" 'systemctl is-active grokbot-shim.service')"
else
  say shim "SKIP (no /root/grokbot/9router-shim/proxy.js on $BOX)"
fi

# 4c. the hunt engine. abh_deploy.sh fetches ABH, applies our patch and wires
#     the brain, but it dies mid-run when router.key is missing or 9router is
#     not answering on :20128 — and this script is `set -eu`, so letting it
#     abort here would stop the provision halfway and leave an unconfigured
#     box. Preflight both hard requirements and skip loudly instead, the way
#     the skills2 and drost steps above already do.
#
#     The FIRST check is the one that matters for a box that already works.
#     blu's /root/Agentic-Bug-Hunter is a plain tree with no .git — it was
#     scp'd, not cloned — so abh_deploy.sh's `[ -d .git ]` test fails and it
#     takes the CLONE branch: `rm -rf` the deployed tree, clone upstream, then
#     apply a patch derived from a commit upstream has since force-pushed
#     away. The tree is gone whether or not the apply succeeds afterwards.
#     The patch reverse-applying cleanly is the honest signal that our edits
#     are already in place, and it does not need a git repo at all.
#     The patch path must be ABSOLUTE: `git -C <dir> apply` resolves a relative
#     one against <dir>, so a relative path silently checks the wrong place.
if ssh -o BatchMode=yes "$BOX" 'git -C /root/Agentic-Bug-Hunter apply --reverse --check /root/Auto-FreeCF/patches/agentic-bug-hunter-blu.patch >/dev/null 2>&1'; then
  say abh "already patched — skipped (re-running would reset --hard first)"
elif ! ssh -o BatchMode=yes "$BOX" 'test -f /root/grokbot/router.key'; then
  say abh "SKIP (no /root/grokbot/router.key on $BOX)"
elif ! ssh -o BatchMode=yes "$BOX" 'curl -sf -o /dev/null -m 8 http://127.0.0.1:20128/api/health'; then
  # Probe /api/health, NOT /v1/models. /v1/models enumerates the entire model
  # list and takes ~15 s to answer, so a short timeout there reports a healthy
  # 9router as dead — which is exactly how this check first failed against a
  # 9router that was up the whole time. /api/health is ~4 ms and returns {"ok":true}.
  say abh "SKIP (9router not answering /api/health on :20128 — start blu-9router, then re-run)"
elif ssh -o BatchMode=yes "$BOX" 'cd /root/Auto-FreeCF && bash scripts/abh_deploy.sh' >/tmp/abh_deploy.log 2>&1; then
  say abh "deployed ($(grep -c '^\[abh\]' /tmp/abh_deploy.log 2>/dev/null || echo 0) steps)"
else
  say abh "FAILED — tail of /tmp/abh_deploy.log:"
  tail -5 /tmp/abh_deploy.log | sed 's/^/              /'
fi

# 5. prove it end to end, from inside the box.
ssh -o BatchMode=yes "$BOX" "export PATH=\"\$HOME/.local/bin:\$HOME/.opencode/bin:\$PATH\"; cd ~ && opencode run --model 9router/$MODEL 'Reply with exactly: BLU-OK'" 2>&1 | tail -1