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
#   R9_FARM_PROXY=http://127.0.0.1:25344 R9_FARM_MAX=4
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
P=${R9_FARM_PROXY:-http://127.0.0.1:25344}   # wireproxy (Cloudflare WARP) on the farm box
N=${R9_FARM_MAX:-4}
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

# 2. farm remotely (camoufox needs a display -> xvfb-run)
ssh -o BatchMode=yes -o ConnectTimeout=20 "$H" \
  "cd ~/Auto-FreeCF && xvfb-run -a ~/venv/bin/python scripts/r9_farm_loop.py run --max $N --proxy '$P' --apply" \
  2>&1 | tail -20

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
