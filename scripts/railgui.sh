#!/usr/bin/env bash
# One-click Railway box menu (zenity). Works on the laptop and on the ASUS.
#   RAILBOX_HOST=asus (default on the laptop) runs railbox.py there over ssh;
#   RAILBOX_HOST=local runs it on this machine (own keys, own 3-boxes/IP/day).
# CLI: railgui.sh <status|shell|setup|run "cmd"|swarm N "cmd"|log|pull>
set -u
cd "$(dirname "$0")/.."
HOST=${RAILBOX_HOST:-$([ "$(hostname)" = NJHERE ] && echo local || echo asus)}
SETUP='cd /app && [ -d work/.git ] || git clone -q --depth 1 https://github.com/Ini-Amin/Kanca-Hub.git work; cd /app/work && git pull -q; nohup sh scripts/railbox_bootstrap.sh > /tmp/boot.log 2>&1 &'

if [ "$HOST" = local ]; then rb() { python3 scripts/railbox.py "$@"; }
else rb() { ssh -o ConnectTimeout=10 "$HOST" "cd ~/Auto-FreeCF && python3 scripts/railbox.py $(printf '%q ' "$@")"; }; fi
box()  { rb ssh -- "bash -lc '$1'" 2>&1 | sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g' | grep -vE 'durable session|agent|^\s*[┌└│]'; }
term() { x-terminal-emulator -e bash -c "$1; read -rp 'Enter to close'" 2>/dev/null || bash -c "$1"; }

act() {
  case "$1" in
    status) rb status ;;
    shell)  if [ "$HOST" = local ]; then term "python3 scripts/railbox.py ssh"; else term "ssh -t $HOST 'cd ~/Auto-FreeCF && python3 scripts/railbox.py ssh'"; fi ;;
    setup)  box "$SETUP echo setup started, ~5 min: railgui.sh log" ;;
    run)    box "cd /app/work && nohup xvfb-run -a /app/venv/bin/python $2 > /tmp/job.log 2>&1 & echo started: tail with railgui.sh log" ;;
    swarm)  rb swarm "$2" "$3" ;;
    log)    box "tail -n 30 /tmp/job.log 2>/dev/null; echo --- boot; tail -n 3 /tmp/boot.log 2>/dev/null" ;;
    pull)   rb pull ;;
    *) echo "usage: $0 <status|shell|setup|run 'scripts/x.py args'|swarm N 'scripts/x.py args'|log|pull>"; return 2 ;;
  esac
}

if [ $# -gt 0 ]; then act "$@"; exit; fi

while c=$(zenity --list --title="Railway boxes ($HOST)" --text="$(rb status 2>&1 | cut -c1-90)" \
          --column=Action --column=What --width=560 --height=360 \
          status "Boxes + minutes left" shell "Open a terminal on the box" setup "Clone repo + install (once per box)" \
          run "Run a script in background" swarm "Start N boxes running the same script" \
          log "Show job / setup log" pull "Download /app/work snapshot"); do
  case "$c" in
    run|swarm)
      cmd=$(zenity --entry --title="$c" --text="Script + args (from /app/work):" \
            --entry-text='scripts/autofarm.py "https://example.com/signup" --mail mailtm --no-proxy') || continue
      if [ "$c" = swarm ]; then
        n=$(zenity --scale --title=swarm --text="How many boxes? (Railway allows ~3 per IP per day)" --min-value=1 --max-value=3 --value=2) || continue
        out=$(act swarm "$n" "$cmd")
      else out=$(act run "$cmd"); fi ;;
    shell) act shell; continue ;;
    *) out=$(act "$c") ;;
  esac
  zenity --text-info --title="$c" --width=760 --height=460 <<<"$out"
done
