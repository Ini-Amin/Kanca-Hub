#!/usr/bin/env bash
# Keep the bug-bounty pipeline running continuously instead of on a 3-hour
# metronome.
#
# WHAT THIS REPLACES: blu-bounty.timer fired abh_hunt.py every 3h with up to 40
# minutes of RandomizedDelaySec, hunting ONE program for 0.5h. That is 8 ticks a
# day, roughly 4 hours of actual hunting, and the gap between ticks was dead
# time. The 0.5h budget was also too small on its own terms: a single run of
# sandbox-api.fireblocks.io spent 378s (6.3 min) inside run_recon alone, so a
# 30-minute budget left one step for every judgement call after recon.
#
# HOW THIS IS DIFFERENT: one long-lived process. Discovery, scope fetch and
# triage repeat back to back; the per-program cap is the only bound. The lock
# below is what makes that safe if a timer still fires the unit by hand - the
# second invocation exits immediately instead of racing the first.
#
# THE GUARD: model_health_watch.py writes model-health.json. If no model in the
# chain answers with a tool call, there is nothing for the engine to reason
# with, so the loop sleeps instead of spending ticks on a chain that cannot
# respond. That file is also the answer to "is the quota gone" without reading
# ninerouter logs.
set -u

LOCK=/var/lock/blu-bounty.loop
HEALTH=${BOUNTY_HEALTH_FILE:-/root/.config/auto-freecf/model-health.json}
ENV_FILE=${ABH_ENV:-/root/abh.env}
HUNT=/root/Auto-FreeCF/scripts/abh_hunt.py
PY=${BOUNTY_PY:-python3}

# Programs per tick. 2, not 1: discovery and the scope fetch are paid again on
# every tick, so one program per tick spends most of the tick on bookkeeping.
PROGRAMS=${BOUNTY_LOOP_PROGRAMS:-2}
HOURS=${BOUNTY_LOOP_HOURS:-1.0}
STEPS=${BOUNTY_LOOP_STEPS:-20}
SLEEP=${BOUNTY_LOOP_SLEEP:-120}

log() { echo "[bounty-loop $(date -u +%H:%M:%S)] $*"; }

# One loop at a time. flock -n: if another copy holds it, this one is a
# duplicate and should vanish quietly (exit 0) rather than error the unit.
exec 9>"$LOCK"
if ! flock -n 9; then
  log "another bounty loop holds $LOCK - exiting"
  exit 0
fi

[ -f "$ENV_FILE" ] && . "$ENV_FILE"
[ -f "$HUNT" ] || { log "missing $HUNT"; exit 1; }

log "starting: ${PROGRAMS} program(s)/tick, ${HOURS}h each, ${STEPS} steps, sleep ${SLEEP}s"

while true; do
  # Guard: if the last health probe found nothing usable, do not start a hunt
  # that can only burn the clock. Stale means older than 2h, i.e. the prober
  # itself is broken - then hunt anyway, because a missing file is not proof of
  # a dead chain.
  if [ -f "$HEALTH" ]; then
    AGE=$(( $(date +%s) - $(stat -c %Y "$HEALTH") ))
    if [ "$AGE" -lt 7200 ]; then
      OK=$("$PY" - "$HEALTH" <<'PYEOF' 2>/dev/null
import json, sys
try:
    print("yes" if json.load(open(sys.argv[1])).get("any_healthy") else "no")
except Exception:
    print("unknown")
PYEOF
)
      if [ "$OK" = "no" ]; then
        log "no healthy model in chain - backing off ${SLEEP}s"
        sleep "$SLEEP"
        continue
      fi
    fi
  fi

  log "tick: hunting ${PROGRAMS} program(s)"
  "$PY" "$HUNT" --platform all --limit 50 \
                --max-programs "$PROGRAMS" --hours "$HOURS" --steps "$STEPS"
  RC=$?
  log "tick done rc=$RC - sleeping ${SLEEP}s"
  # A hard failure (missing deploy, bad env) repeats every 2 minutes forever.
  # Back off progressively so the journal stays readable.
  if [ "$RC" -ne 0 ]; then
    sleep $(( SLEEP * 5 ))
  else
    sleep "$SLEEP"
  fi
done
