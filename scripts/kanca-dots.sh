#!/usr/bin/env bash
# Kanca-Dots: start 9Router (:20129) + ZCode bridge + Luvus web + the :20128 front door, open panes.
set -u
cd "$(dirname "$0")/.."
up() { ss -ltn | grep -q "127.0.0.1:$1 \|0.0.0.0:$1 \|\*:$1 "; }
D=/tmp/opencode; mkdir -p "$D"

systemctl --user start app-9router@autostart zcode-bridge deepseek-bridge kanca-dots 2>/dev/null
up 4174 || nohup luvus web --control --origin http://panes.localhost:20128 --no-open >"$D/luvus-web.log" 2>&1 &
sleep 3
# One app window (no tabs/URL bar) via Brave app mode; own profile so it gets its own taskbar icon.
[ "${1:-}" = --no-open ] || nohup brave-browser --app="http://panes.localhost:20128/$(grep -oE '#pair=[^ ]+' "$D/luvus-web.log" | tail -1)" \
  --user-data-dir="$HOME/.local/share/kanca-dots/window" --class=Kanca-Dots >/dev/null 2>&1 &
echo "9router: http://localhost:20128   panes: http://panes.localhost:20128"
