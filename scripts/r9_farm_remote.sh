#!/usr/bin/env bash
# Farm TokenHarbor (THK) on LightVela, then inject the keys into THIS box's 9Router.
#
# RUN THIS ON blu. LightVela does the farming (it has the always-on WARP egress
# and the camoufox venv); blu owns the 9Router DB that the bounty workers call.
#
# WHY THE SPLIT: `thk inject`/`sync` edit ~/.9router/db/data.sqlite. Only one
# host should own that file. blu owns it now, because grok-bot, the zcode bridge
# and bounty_run all run there, so the keys must land where they are used. If the
# farm box also wrote the DB, two sqlite writers would race and the loser's keys
# would silently vanish.
#
#   r9_farm_remote.sh                    farm N accounts on LightVela, inject here
#   R9_FARM_HOST=lightvela r9_farm_remote.sh
#   R9_FARM_PROXY=vpngate:JP R9_FARM_MAX=4
#
# The remote box must already have a working masked egress; proxy_verify on the
# box proves it before the batch runs (r9_farm_loop does that itself).
#
# WHY `git pull` IS SAFE NOW: the repo was private and LightVela had no git
# credentials, so `git pull` failed and the farm silently ran OLD code (the
# LightVela slider had drifted from ours). It is public now. Even so this script
# still VERIFIES the code it is about to run instead of trusting the pull: a
# silent stale-code run is exactly the failure this farm cannot afford.
set -u
H=${R9_FARM_HOST:-lightvela}
# Egress: a VPN Gate relay, NOT the WARP wireproxy. THK refuses the shared WARP
# range and caps signups per network; a fresh relay per account is what lets the
# batch finish (measured 2026-10-19: WARP -> 0/4, vpngate -> account created,
# 1 key injected). A URL here still works if you have a residential pool.
# WARP is still what the box uses for everything else, on 127.0.0.1:25344.
P=${R9_FARM_PROXY:-vpngate:JP}
N=${R9_FARM_MAX:-4}
PER_IP=${R9_FARM_PER_IP:-1}   # rotate relay every N accounts (THK caps after ~1-2)
REPO=${R9_FARM_REPO:-${HOME:-/root}/Auto-FreeCF}
BRANCH=${R9_FARM_BRANCH:-main}

# The slider solver is the one file whose drift is both silent and fatal: an old
# solver does not error, it just fails every captcha and the batch reports zero.
# Compare its hash on the farm box against ours and refuse to farm on a mismatch.
LOCAL_SLIDER=$(md5sum "$REPO/scripts/captcha_slider.py" 2>/dev/null | cut -d' ' -f1)

echo "[farm] host=$H egress=$P n=$N branch=$BRANCH  $(date '+%F %T')"

# 1. sync the farm box to our commit and prove the solver matches
ssh -o BatchMode=yes -o ConnectTimeout=20 "$H" \
  "cd ~/Auto-FreeCF || exit 11
   git fetch -q origin || exit 12
   git reset --hard -q origin/$BRANCH || exit 13
   echo \"[farm] box commit: \$(git log --oneline -1)\"
   echo \"[farm] box slider: \$(md5sum scripts/captcha_slider.py | cut -d' ' -f1)\"" 2>&1 | tee /tmp/r9-sync.$$ 
SYNC_RC=${PIPESTATUS[0]}
if [ "$SYNC_RC" -ne 0 ]; then
  echo "[farm] ABORT: could not sync $H to origin/$BRANCH (rc=$SYNC_RC)"
  rm -f /tmp/r9-sync.$$; exit 1
fi
BOX_SLIDER=$(grep -o 'box slider: [0-9a-f]*' /tmp/r9-sync.$$ | tail -1 | cut -d' ' -f3)
rm -f /tmp/r9-sync.$$
if [ -n "$LOCAL_SLIDER" ] && [ -n "$BOX_SLIDER" ] && [ "$LOCAL_SLIDER" != "$BOX_SLIDER" ]; then
  echo "[farm] ABORT: slider drift — local=$LOCAL_SLIDER box=$BOX_SLIDER"
  echo "[farm] (a stale solver fails every captcha without erroring)"
  exit 1
fi

# 2. farm remotely (camoufox needs a display -> xvfb-run), DETACHED.
#    The batch rotates egress IPs, and every relay bring-up replaces the box's
#    default route. That has killed this ssh channel mid-batch ("client_loop:
#    send disconnect: Broken pipe"), which SIGHUPs the remote loop: the run
#    produced 2 of 4 keys and 1 of them was stranded because step 3 collected
#    while the batch was still going. So: start it with setsid, log on the box,
#    and poll over FRESH connections until nothing is left running.
RLOG='$HOME/r9-farm.log'
ssh -o BatchMode=yes -o ConnectTimeout=20 "$H" \
  "cd ~/Auto-FreeCF || exit 11
   rm -f $RLOG
   setsid nohup xvfb-run -a ~/venv/bin/python scripts/r9_farm_loop.py run --max $N --proxy '$P' --per-ip $PER_IP --farm-only --apply > $RLOG 2>&1 < /dev/null &
   echo \"[farm] remote started pid=\$! log=$RLOG\"" \
  2>&1 || echo "[farm] WARN: start ssh failed (the farm may still be running detached)"

# Bracket the first letter of each alternative: the remote shell's own cmdline
# contains this pattern, and an un-bracketed `pgrep -f` matches IT, so the poll
# would never see the farm finish (measured: 2 self-matches vs 0).
FARM_PAT='[r]9_farm_loop.py|[t]okenharbor.cli|[k]ancahub.py thk'
# Wait for the remote farm to actually finish. The exit status distinguishes the
# three cases that matter: 0 = still running, 1 = pgrep found nothing (done),
# anything else = the connection itself failed (a relay bring-up can drop it),
# which must NOT be read as "done" -- doing so collected account.json 80s into a
# 20-minute batch and stranded the keys it made afterwards.
WAITED=0
for _ in $(seq 1 300); do
  ssh -o BatchMode=yes -o ConnectTimeout=20 "$H" "pgrep -f '$FARM_PAT' >/dev/null 2>&1"
  case $? in
    0) ;;      # farm still running
    1) break ;;  # no matching process: it is done
    *) echo "[farm] poll ssh failed; retrying in 10s" ;;
  esac
  sleep 10
  WAITED=$((WAITED + 10))
done
echo "[farm] remote farm finished (waited ${WAITED}s)"
ssh -o BatchMode=yes -o ConnectTimeout=20 "$H" "tail -24 $RLOG" 2>&1 | tail -24

# 3. pull the farm's account store and merge it into ours (dedupe on email+key)
mkdir -p "$REPO/harbor"
scp -q "$H:~/Auto-FreeCF/harbor/account.json" "$REPO/harbor/account.json.remote" 2>/dev/null \
  || { echo "[farm] no remote account.json yet"; exit 0; }
python3 - "$REPO" <<'PY'
import json, pathlib, sys
repo = pathlib.Path(sys.argv[1])
rem_f = repo / "harbor/account.json.remote"
loc_f = repo / "harbor/account.json"

def load(p):
    try:
        d = json.loads(p.read_text() or "[]")
    except Exception:
        return []
    return d if isinstance(d, list) else [d]

loc, rem = load(loc_f), load(rem_f)
seen = {(a.get("email"), a.get("api_key")) for a in loc}
new = [a for a in rem if (a.get("email"), a.get("api_key")) not in seen]
loc_f.write_text(json.dumps(loc + new, indent=2))
print(f"[farm] merged {len(new)} new key(s); harbor store now {len(loc) + len(new)}")
PY

# 4. store them in THIS box's 9Router (the one the bounty workers call)
python3 "$REPO/scripts/kancahub.py" thk inject --verify 2>&1 | tail -8
python3 "$REPO/scripts/kancahub.py" thk sync --prune 2>&1 | tail -5

# 5. prove the router actually serves the new keys
CODE=$(curl -s -o /dev/null -w '%{http_code}' -m 20 \
  http://127.0.0.1:20128/v1/models 2>/dev/null)
echo "[farm] 9router /v1/models -> ${CODE:-no-response}"
echo "[farm] done $(date '+%F %T')"
