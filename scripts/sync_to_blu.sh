#!/usr/bin/env bash
# scripts/sync_to_blu.sh — Push all freshly mined assets to blu cloud sandbox
set -euo pipefail

echo "=== Syncing Auto-FreeCF Mined Assets to blu ==="

# Check connectivity
echo "[1/4] Checking SSH connection to blu..."
ssh -o ConnectTimeout=8 blu "uptime"
# Sync distilled rules and mass engine outputs
echo "[2/5] Syncing GLM-5.3 Distilled Rules & Engine Data..."
ssh blu "mkdir -p ~/.config/auto-freecf/glm_engine ~/.config/auto-freecf/bounty ~/.config/auto-freecf/pocindex"
rsync -avz --progress ~/.config/auto-freecf/glm_engine/ blu:~/.config/auto-freecf/glm_engine/

# Sync scoped bounty targets & cve database
echo "[3/5] Syncing Real Scoped Targets & Enriched CVE Database..."
rsync -avz --progress ~/.config/auto-freecf/bounty/ blu:~/.config/auto-freecf/bounty/
rsync -avz --progress ~/.config/auto-freecf/pocindex/ blu:~/.config/auto-freecf/pocindex/
rsync -avz --progress ~/.config/auto-freecf/cve.db blu:~/.config/auto-freecf/cve.db

# Sync audit reports & kancaproxy binary
echo "[4/5] Syncing Reports..."
ssh blu "mkdir -p ~/Auto-FreeCF/reports"
rsync -avz --progress reports/deduplication_audit.md reports/boundary_test_results.json blu:~/Auto-FreeCF/reports/

echo "[5/5] Syncing kancaproxy binary..."
ssh blu "mkdir -p ~/.local/bin"
rsync -avz ~/.local/bin/kancaproxy blu:~/.local/bin/kancaproxy

# Sync CodeBuddy bridge & patched ZCode scripts to blu
echo "[6/6] Syncing Bridges & Patched Scripts to blu..."
ssh blu "mkdir -p ~/Auto-FreeCF/scripts"
rsync -avz scripts/codebuddy_bridge.py scripts/zcode_bridge_server.py scripts/zcode_egress_rotator.py blu:~/Auto-FreeCF/scripts/

echo "=== Sync Complete! Everything is ready on blu. ==="
