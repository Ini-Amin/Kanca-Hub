#!/usr/bin/env bash
# Kanca-Dots: start 9Router (:20129) + ZCode bridge + Luvus web + the :20128 front door, open panes.
set -u
cd "$(dirname "$0")/.."
NODE=~/.local/share/node24/bin/node
up() { ss -ltn | grep -q "127.0.0.1:$1 \|0.0.0.0:$1 \|\*:$1 "; }
D=/tmp/opencode; mkdir -p "$D"

up 20129 || nohup "$NODE" ~/.npm-global/lib/node_modules/9router/cli.js --port 20129 --host 127.0.0.1 --skip-update >"$D/9router.log" 2>&1 &
systemctl --user start zcode-bridge deepseek-bridge 2>/dev/null
up 4174 || nohup luvus web --control --origin http://panes.localhost:20128 --no-open >"$D/luvus-web.log" 2>&1 &
up 20128 || nohup "$NODE" scripts/kanca_dots.mjs >"$D/kanca-dots.log" 2>&1 &
sleep 3
[ "${1:-}" = --no-open ] || xdg-open "http://panes.localhost:20128/$(grep -oE '#pair=[^ ]+' "$D/luvus-web.log" | tail -1)" >/dev/null 2>&1 &
echo "9router: http://localhost:20128   panes: http://panes.localhost:20128"
