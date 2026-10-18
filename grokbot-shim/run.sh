#!/bin/bash
# grok-bot wired through the 9router shim (Fedora, extracted .deb — not installed).
SHIM_DIR="$(cd "$(dirname "$0")" && pwd)"
export ROUTER_URL="${ROUTER_URL:-http://localhost:20128}"
export ROUTER_MODEL="${ROUTER_MODEL:-ag/gemini-3.8-flash-high}"
if [ -z "$ROUTER_API_KEY" ]; then
  # pull the key from opencode config (9router provider)
  ROUTER_API_KEY=$(node -e '
    try {
      const c = require(process.env.HOME + "/.config/opencode/opencode.json");
      process.stdout.write(c.provider["9router"]?.options?.apiKey || "");
    } catch {}
  ')
  export ROUTER_API_KEY
fi
# a stale shim from a previous run holding the port is the usual EADDRINUSE
pkill -f "node.*proxy.js" 2>/dev/null && sleep 1
node "$SHIM_DIR/proxy.js" &
SHIM_PID=$!
sleep 1
export ROUTER_API_KEY="${ROUTER_API_KEY:-}"
export ROUTER_API_KEY="${ROUTER_API_KEY:-}"
export SAND_BACKEND_URL="http://127.0.0.1:8790"
export CURSOR_API_BASE_URL="http://127.0.0.1:8790"
BOT="${GROKBOT_BIN:-/home/amen/grokre/root/opt/Grok Bot/grok-bot}"
"$BOT" --no-sandbox || "$BOT"
kill $SHIM_PID 2>/dev/null
