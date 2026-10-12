#!/usr/bin/env bash
# Heavy jobs on the LightVela box (always-on, 2 CPU / 7 GB). Laptop side, over `ssh lightvela`.
#   lvjob.sh run "scripts/autofarm.py URL --mail mailtm --no-proxy"   start in background, prints job name
#   lvjob.sh ls                                                       running + finished jobs
#   lvjob.sh log NAME | stop NAME | sync                              (sync = git pull on the box)
# ponytail: one job per call, no queue; add a queue when jobs start fighting over 2 CPUs.
set -eu
H=${LV_HOST:-lightvela}
on() { ssh -o BatchMode=yes -o ConnectTimeout=20 "$H" "$1" </dev/null; }
case "${1:-ls}" in
  run)  n="j$(date +%m%d-%H%M%S)"
        on "mkdir -p ~/jobs && cd ~/Auto-FreeCF && git pull -q; nohup sh -c $(printf %q "PYTHONDONTWRITEBYTECODE=1 xvfb-run -a ~/venv/bin/python $2; echo \"[exit \$?]\"") > ~/jobs/$n.log 2>&1 & echo \$! > ~/jobs/$n.pid; echo $n started" ;;
  ls)   on 'cd ~/jobs 2>/dev/null || exit 0; for p in *.pid; do [ -e "$p" ] || continue; n=${p%.pid}; if kill -0 $(cat $p) 2>/dev/null; then s=running; else s=$(tail -n1 $n.log | grep -o "exit [0-9]*" || echo done); fi; echo "$n $s"; done' ;;
  log)  on "tail -n ${3:-40} ~/jobs/$2.log" ;;
  stop) on "pkill -P \$(cat ~/jobs/$2.pid) ; kill \$(cat ~/jobs/$2.pid) && echo stopped $2" ;;
  sync) on 'cd ~/Auto-FreeCF && git pull -q && git log --oneline -1' ;;
  *)    sed -n 2,6p "$0"; exit 2 ;;
esac
