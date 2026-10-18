#!/usr/bin/env bash
# blu_tunnel_guard.sh — un-jam blu-tunnel.service when it hits a terminal state.
#
# THE PROBLEM THIS SOLVES
# -----------------------
# `blu-tunnel.service` is `Restart=on-failure` with StartLimitBurst=5 /
# StartLimitIntervalSec=300. That rate limit is deliberate and correct — without
# it a permanently-failing ssh keeps the unit in "activating" forever and, being
# ordered before default.target, holds the whole user manager's job queue open
# (the "I open the browser and it just hangs" bug documented in the unit).
#
# But `start-limit-hit` is TERMINAL. blu is a Manus sandbox that drops off the
# tailnet for minutes at a time; a single 15-minute outage is enough to burn all
# 5 retries inside the 300 s window. The unit then sits `failed` FOREVER, even
# after blu comes back. Nothing restarts it, no timer notices, and the failure
# presents as "the model is down" — measured 2026-10-18, 36 minutes dead with
# blu healthy the whole time.
#
# So: the rate limit stays, and this guard clears the latch once blu is actually
# reachable again. It never fights a live tunnel — it only acts when the unit is
# in a terminal state AND the peer answers.
#
# Run by blu-tunnel-guard.timer (every 2 min). Cheap: one `tailscale ping`.
set -uo pipefail

UNIT="${BLU_TUNNEL_UNIT:-blu-tunnel.service}"
PEER="${BLU_PEER:-blu}"
LOG="${HOME}/.config/auto-freecf/tunnel-guard.log"
mkdir -p "$(dirname "$LOG")"

say() { printf '%s %s\n' "$(date +'%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"; }

# Keep the log bounded without a rotation daemon.
if [ -f "$LOG" ] && [ "$(stat -c %s "$LOG" 2>/dev/null || echo 0)" -gt 200000 ]; then
    tail -n 400 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

state=$(systemctl --user is-active "$UNIT" 2>/dev/null)

# Healthy or merely restarting on its own: leave it alone.
case "$state" in
    active|activating|reloading) exit 0 ;;
esac

# Only a terminal latch is ours to clear. `failed` after start-limit-hit is the
# case that strands the tunnel; a plain transient failure will restart itself.
result=$(systemctl --user show "$UNIT" -p Result --value 2>/dev/null)
if [ "$result" != "start-limit-hit" ] && [ "$state" != "failed" ]; then
    exit 0
fi

# Do not reset the latch while the peer is still unreachable: that just re-arms
# the same five failing retries and burns the window again.
if ! timeout 10 tailscale ping --c 1 --timeout 4s "$PEER" >/dev/null 2>&1; then
    say "$UNIT stuck (${result:-$state}) but $PEER unreachable — not resetting"
    exit 0
fi

say "$UNIT stuck (${result:-$state}); $PEER is reachable — resetting and starting"
systemctl --user reset-failed "$UNIT" 2>/dev/null
systemctl --user start "$UNIT" 2>/dev/null

sleep 5
if systemctl --user is-active --quiet "$UNIT"; then
    say "$UNIT RECOVERED"
else
    say "$UNIT did not come up after reset — will retry next tick"
fi
