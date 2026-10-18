#!/usr/bin/env bash
# blu-keepalive.sh — keeps blu awake, and reports (does not fight) the tunnel.
#
# blu is a Manus sandbox that pauses after ~30 min idle and drops off the
# tailnet for minutes at a time. This loop's job is narrow and boring:
#
#   1. idle heartbeat  — tiny CPU + log write so the sandbox never idle-pauses
#   2. tunnel watch    — verify blu:20128 still answers, and LOG it when it does
#                        not, so a silent model outage is diagnosable later
#   3. net snapshot    — occasional tailnet/egress record
#
# OWNERSHIP: the reverse tunnel is owned by the DESKTOP
# (`~/.config/systemd/user/blu-tunnel.service`, ssh -R 20128), because the thing
# being forwarded (9router) lives there and its lifecycle should follow it.
# An earlier version of this script also tried to re-establish the tunnel from
# this side; that is wrong — two sides opening -R to the same port fight over
# it, and a blu-side ssh cannot restart a tunnel whose listener died on the
# desktop. So: watch here, own there. If the port is down for several ticks we
# say so loudly and stop pretending.
#
# Deliberately NOT in here: anything touching z.ai. This runs forever on a box
# we do not control and must stay cheap.
set -uo pipefail

STATE="$HOME/.config/auto-freecf"
LOG="$STATE/idle.log"
TUN_LOG="$STATE/tunnel.log"
mkdir -p "$STATE"

TUNNEL_PORT="${BLU_TUNNEL_PORT:-20128}"
# Consecutive failed ticks before we declare the tunnel broken.
TUNNEL_ALERT_AFTER="${BLU_TUNNEL_ALERT_AFTER:-4}"

say() { printf '%s %s\n' "$(date +'%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG"; }
tun_say() { printf '%s %s\n' "$(date +'%Y-%m-%d %H:%M:%S')" "$*" >>"$TUN_LOG"; }
cap() { tail -n 400 "$1" >"$1.tmp" 2>/dev/null && mv "$1.tmp" "$1"; }

# A listening port is necessary but not sufficient: the ssh could be up with a
# dead forward. Ask the router itself, cheaply, and treat a non-200 as down.
tunnel_ok() {
    ss -tln 2>/dev/null | grep -q "127.0.0.1:${TUNNEL_PORT} " || return 1
    local code
    code=$(timeout 20 curl -s -o /dev/null -w '%{http_code}' -m 15 \
        "http://127.0.0.1:${TUNNEL_PORT}/v1/models" 2>/dev/null)
    [ "$code" = "200" ]
}

say "keepalive start (pid $$, watching tunnel :${TUNNEL_PORT}; owned by desktop)"
cap "$LOG"; cap "$TUN_LOG"

n=0
down=0
while true; do
    n=$((n + 1))
    say "tick $n"
    cap "$LOG"

    # idle heartbeat: a write plus a brief spin + tailnet packet so Manus NAT keeps tailscaled warm
    i=0; while [ $i -lt 20000 ]; do i=$((i + 1)); done
    timeout 4 tailscale ping -c 1 --timeout=2s 100.104.217.54 >/dev/null 2>&1 || true # ponytail: single peer ping, add fallback peer list if asus is retired

    if tunnel_ok; then
        if [ "$down" -ge "$TUNNEL_ALERT_AFTER" ]; then
            tun_say "tunnel RECOVERED on :${TUNNEL_PORT} after ${down} failed ticks"
        fi
        down=0
    else
        down=$((down + 1))
        # Log the first failure and then once per alert window, not every tick.
        if [ "$down" -eq 1 ] || [ $((down % TUNNEL_ALERT_AFTER)) -eq 0 ]; then
            tun_say "tunnel DOWN on :${TUNNEL_PORT} (${down} consecutive ticks) — desktop unit blu-tunnel.service owns it"
        fi
    fi

    # occasional network snapshot (every ~10 ticks = ~7.5 min)
    if [ $((n % 10)) -eq 0 ]; then
        local_ip=$(timeout 8 tailscale ip -4 2>/dev/null | head -1)
        say "net tailscale=${local_ip:-?} tunnel_down_ticks=${down}"
        cap "$LOG"
    fi

    sleep 45
done
