#!/usr/bin/env bash
# Farm THK on a masked remote box, then pull the keys home and inject them into
# OUR 9Router. Split because `thk inject`/`sync` edit ~/.9router/db/data.sqlite,
# which only the laptop has — LightVela farms, the laptop stores.
#
#   r9_farm_remote.sh              farm on LightVela (WARP egress), inject here
#   R9_FARM_HOST=... R9_FARM_PROXY=... R9_FARM_MAX=N   override defaults
#
# The remote box must already have a working masked egress; proxy_verify on the
# box proves it before the batch runs (r9_farm_loop does that itself).
set -u
H=${R9_FARM_HOST:-lightvela}
P=${R9_FARM_PROXY:-http://127.0.0.1:25344}   # wireproxy (Cloudflare WARP) on the box
N=${R9_FARM_MAX:-4}
REPO=$HOME/Auto-FreeCF

echo "[farm] host=$H egress=$P n=$N  $(date '+%F %T')"

# 1. farm remotely (camoufox needs a display -> xvfb-run), pull the latest scripts first
ssh -o BatchMode=yes -o ConnectTimeout=20 "$H" \
  "cd ~/Auto-FreeCF && git pull -q 2>/dev/null; \
   xvfb-run -a ~/venv/bin/python scripts/r9_farm_loop.py run --max $N --proxy '$P' --apply" \
  2>&1 | tail -20

# 2. pull the farm's account store and merge it into ours (dedupe on email+key)
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

# 3. store them in OUR 9Router
"$REPO/scripts/kancahub.py" thk inject --verify 2>&1 | tail -8
"$REPO/scripts/kancahub.py" thk sync --prune 2>&1 | tail -5
echo "[farm] done $(date '+%F %T')"