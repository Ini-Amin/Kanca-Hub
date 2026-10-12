#!/usr/bin/env bash
# One-click Railway box menu (zenity). The ASUS owns the boxes, so every action
# runs railbox.py there over ssh. CLI: railgui.sh <status|shell|setup|run "cmd"|log|pull>
set -u
HOST=${RAILBOX_HOST:-asus}
RB="cd ~/Auto-FreeCF && python3 scripts/railbox.py"
SETUP='cd /app && [ -d work/.git ] || git clone -q --depth 1 https://github.com/Ini-Amin/Kanca-Hub.git work; cd /app/work && git pull -q; nohup sh scripts/railbox_bootstrap.sh > /tmp/boot.log 2>&1 &'

box()  { ssh -o ConnectTimeout=10 "$HOST" "$RB ssh -- \"bash -lc '$1'\"" 2>&1 | sed 's/\x1b\[[0-9;?]*[a-zA-Z]//g' | grep -vE 'durable session|agent|^\s*[┌└│]'; }
term() { x-terminal-emulator -e bash -c "$1; read -rp 'Enter to close'" 2>/dev/null || bash -c "$1"; }

act() {
  case "$1" in
    status) ssh "$HOST" "$RB status" ;;
    shell)  term "ssh -t $HOST '$RB ssh'" ;;
    setup)  box "$SETUP echo setup started, ~5 min: railgui.sh log" ;;
    run)    box "cd /app/work && nohup xvfb-run -a /app/venv/bin/python $2 > /tmp/job.log 2>&1 & echo started: tail with railgui.sh log" ;;
    log)    box "tail -n 30 /tmp/job.log 2>/dev/null; echo --- boot; tail -n 3 /tmp/boot.log 2>/dev/null" ;;
    pull)   ssh "$HOST" "$RB pull" ;;
    *) echo "usage: $0 <status|shell|setup|run 'scripts/x.py args'|log|pull>"; return 2 ;;
  esac
}

if [ $# -gt 0 ]; then act "$@"; exit; fi

while c=$(zenity --list --title="Railway box" --text="$(ssh "$HOST" "$RB status" 2>&1 | cut -c1-90)" \
          --column=Action --column=What --width=560 --height=330 \
          status "Box + minutes left" shell "Open a terminal on the box" setup "Clone repo + install (once per box)" \
          run "Run a script in background" log "Show job / setup log" pull "Download /app/work snapshot"); do
  case "$c" in
    run) cmd=$(zenity --entry --title="Run on box" --text="Script + args (from /app/work):" \
               --entry-text='scripts/autofarm.py "https://example.com/signup" --mail mailtm --no-proxy') || continue
         out=$(act run "$cmd") ;;
    shell) act shell; continue ;;
    *) out=$(act "$c") ;;
  esac
  zenity --text-info --title="$c" --width=760 --height=460 <<<"$out"
done
