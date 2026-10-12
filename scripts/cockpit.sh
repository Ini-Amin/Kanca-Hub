#!/usr/bin/env bash
# One-click cockpit: a Luvus tab with agent + logs panes, an opencode web chat
# (Grok-Bot-style visual app, every 9Router model), and the Luvus browser view.
#   scripts/cockpit.sh          build layout + open both web UIs
#   scripts/cockpit.sh --no-web layout only
set -u
cd "$(dirname "$0")/.."
WEB_PORT=${COCKPIT_WEB_PORT:-4096}
id() { python3 -c "import json,sys; print(json.load(sys.stdin)['result']['pane'])"; }

tab=$(luvus tab new | python3 -c "import json,sys; print(json.load(sys.stdin)['result']['tab'])")
luvus tab rename cockpit --tab "$tab" >/dev/null
main=$(luvus pane list --all-tabs | python3 -c "
import json,sys; print([p['pane'] for p in json.load(sys.stdin)['result']['panes'] if p['tab'] == '$tab'][0])")
right=$(luvus pane split "$main" --right --no-focus | id)
bottom=$(luvus pane split "$main" --down --no-focus | id)
right2=$(luvus pane split "$right" --down --no-focus | id)

luvus pane run "$main"   'opencode' >/dev/null
luvus pane run "$right"  'watch -n 30 "scripts/railgui.sh status | cut -c1-70"' >/dev/null
luvus pane run "$right2" 'ssh asus journalctl --user -fu tencent-watch -n 20 -o cat' >/dev/null
luvus pane run "$bottom" 'watch -n 60 "curl -s -m5 -o /dev/null -w %{http_code} localhost:20128/v1/models; echo \" 9router\"; tail -n 5 results/autofarm_accounts.json"' >/dev/null
for p in "$main:agent" "$right:railway" "$right2:tencent" "$bottom:status"; do
  luvus pane name "${p#*:}" --pane "${p%%:*}" >/dev/null
done
luvus pane focus "$main" >/dev/null

[ "${1:-}" = --no-web ] && exit 0
# ponytail: loopback only; add --hostname 0.0.0.0 + auth before exposing on LAN/Tailscale.
ss -ltn | grep -q ":$WEB_PORT " || nohup opencode serve --port "$WEB_PORT" >/tmp/opencode/cockpit-web.log 2>&1 &
sleep 2
xdg-open "http://127.0.0.1:$WEB_PORT" >/dev/null 2>&1 &
luvus web --control >/tmp/opencode/cockpit-luvus.log 2>&1 &
echo "chat: http://127.0.0.1:$WEB_PORT   panes: see /tmp/opencode/cockpit-luvus.log"
