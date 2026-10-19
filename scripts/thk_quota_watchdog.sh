#!/usr/bin/env bash
# Fire the THK farm when zcode quota runs low.
# Reads ~/.zcode-bridge/balance.json (written by `zcode.py watch`): counts
# accounts with remaining_units > 0. Below the threshold, start
# blu-r9-farm.service (which farms TokenHarbor keys on LightVela and injects
# them into this box's 9router). Cooldown: skip if a farm ran in the last 6h.
set -u
THRESH=${THK_FARM_THRESHOLD:-8}
COOLDOWN_H=${THK_FARM_COOLDOWN_H:-6}
B="${HOME:-/root}/.zcode-bridge/balance.json"
STAMP="/var/tmp/thk-farm-last"

[ -f "$B" ] || { echo "no balance.json"; exit 0; }

ALIVE=$(python3 - "$B" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
alive = set()
for email, rec in d.items():
    for b in rec.get("buckets", []):
        if (b.get("remaining_units") or 0) > 0:
            alive.add(email)
            break
print(len(alive))
PY
)
echo "accounts with quota: $ALIVE (threshold $THRESH)"

if [ "$ALIVE" -ge "$THRESH" ]; then exit 0; fi

# cooldown
if [ -f "$STAMP" ]; then
  AGE=$(( ($(date +%s) - $(stat -c %Y "$STAMP")) / 3600 ))
  [ "$AGE" -lt "$COOLDOWN_H" ] && { echo "farm ran ${AGE}h ago; cooldown"; exit 0; }
fi

echo "quota low -> starting blu-r9-farm.service"
touch "$STAMP"
systemctl start blu-r9-farm.service
