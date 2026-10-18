#!/bin/bash
# grokbot-svc.sh — ONE supervisor for the grok-bot stack on blu.
#
#   grokbot-svc.sh start|stop|status|logs
#
# Pieces:
#   shim  : 8790  (gRPC-Web -> 9router, also OpenAI /v1 for 9router node use)
#   app   : grok-bot under Xvfb :100 (+ x11vnc :5900 for viewing)
#   router: reached at 127.0.0.1:20128 via the reverse ssh tunnel from the laptop
#
# Idempotent: safe to run repeatedly; stale processes are reaped by port, not by
# pkill -f (which matches this script's own command line and kills the session).
set -u

HOME_DIR=/root/grokbot
BOT="$HOME_DIR/Grok Bot/grok-bot"
SHIM="$HOME_DIR/9router-shim/proxy.js"
DISPLAY_NUM=:100
VNC_PORT=5900
SHIM_PORT=8790
ROUTER_URL="${ROUTER_URL:-http://127.0.0.1:20128}"
ROUTER_MODEL="${ROUTER_MODEL:-ag/gemini-3.8-flash-high}"
ROUTER_KEY="${ROUTER_API_KEY:-$(cat "$HOME_DIR/router.key" 2>/dev/null)}"
LOGDIR=/var/log/grokbot

mkdir -p "$LOGDIR"

port_pid() { ss -tlnp 2>/dev/null | grep ":$1 " | grep -oP 'pid=\K[0-9]+' | head -1; }

start() {
  # --- 1. Xvfb (shared display so x11vnc can attach to the same one) ---
  if ! pgrep -f "Xvfb $DISPLAY_NUM" >/dev/null; then
    setsid nohup Xvfb $DISPLAY_NUM -screen 0 1440x900x24 -nolisten tcp -ac >"$LOGDIR/xvfb.log" 2>&1 &
    sleep 1
    echo "xvfb started on $DISPLAY_NUM"
  fi
  # wait for the X socket, otherwise Electron dies with "Missing X server"
  for i in $(seq 1 20); do
    [ -S "/tmp/.X11-unix/X${DISPLAY_NUM#:}" ] && break
    sleep 0.5
  done

  # --- 2. shim ---
  local sp; sp=$(port_pid $SHIM_PORT)
  if [ -n "$sp" ]; then kill -9 "$sp" 2>/dev/null; sleep 0.5; fi
  ROUTER_URL="$ROUTER_URL" ROUTER_MODEL="$ROUTER_MODEL" ROUTER_API_KEY="$ROUTER_KEY" \
    setsid nohup node "$SHIM" >"$LOGDIR/shim.log" 2>&1 &
  sleep 2
  if [ -z "$(port_pid $SHIM_PORT)" ]; then
    echo "SHIM FAILED — log:"; tail -5 "$LOGDIR/shim.log"; return 1
  fi
  echo "shim up on :$SHIM_PORT -> $ROUTER_URL ($ROUTER_MODEL)"

  # --- 3. app ---
  if ! pgrep -f "Grok Bot/grok-bot" >/dev/null; then
    DISPLAY=$DISPLAY_NUM SAND_BACKEND_URL="http://127.0.0.1:$SHIM_PORT" \
      CURSOR_API_BASE_URL="http://127.0.0.1:$SHIM_PORT" \
      setsid nohup "$BOT" --no-sandbox --disable-gpu --remote-debugging-port=9222 >"$LOGDIR/bot.log" 2>&1 &
    sleep 6
    pgrep -f "Grok Bot/grok-bot" >/dev/null && echo "app started on $DISPLAY_NUM" || { echo "APP FAILED — log:"; tail -20 "$LOGDIR/bot.log"; return 1; }
  else
    echo "app already running"
  fi

  # --- 4. vnc ---
  if ! pgrep -x x11vnc >/dev/null; then
    setsid nohup x11vnc -display $DISPLAY_NUM -rfbport $VNC_PORT -nopw -shared -forever \
      >"$LOGDIR/x11vnc.log" 2>&1 &
    sleep 1
    pgrep -x x11vnc >/dev/null && echo "vnc on :$VNC_PORT" || echo "vnc FAILED"
  else
    echo "vnc already running"
  fi
}

stop() {
  for p in $(pgrep -f "Grok Bot/grok-bot") $(port_pid $SHIM_PORT) $(pgrep -x x11vnc); do
    kill "$p" 2>/dev/null
  done
  sleep 1
  for p in $(pgrep -f "Grok Bot/grok-bot") $(port_pid $SHIM_PORT) $(pgrep -x x11vnc); do
    kill -9 "$p" 2>/dev/null
  done
  echo "stopped"
}

status() {
  echo "shim  :8790  $( [ -n "$(port_pid $SHIM_PORT)" ] && echo UP || echo DOWN )"
  echo "app         $( pgrep -f 'Grok Bot/grok-bot' >/dev/null && echo UP || echo DOWN )"
  echo "xvfb $DISPLAY_NUM $( pgrep -f "Xvfb $DISPLAY_NUM" >/dev/null && echo UP || echo DOWN )"
  echo "vnc   :$VNC_PORT $( pgrep -x x11vnc >/dev/null && echo UP || echo DOWN )"
  echo "router       $( curl -s -m 25 -o /dev/null -w '%{http_code}' "$ROUTER_URL/v1/models" -H "Authorization: Bearer $ROUTER_KEY" 2>/dev/null )"
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; sleep 1; start ;;
  status) status ;;
  logs) tail -n 40 "$LOGDIR"/*.log ;;
  *) echo "usage: $0 start|stop|restart|status|logs"; exit 2 ;;
esac
