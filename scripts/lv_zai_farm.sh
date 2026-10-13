#!/usr/bin/env bash
# Farm z.ai accounts on LightVela (always-on, masked WARP egress) and land them
# on the laptop, where the ZCode bridge lives.
#
# Why the split: the signup browser needs a masked egress and a display, both of
# which LightVela has; minting the ZCode JWT needs the CDP Chrome + the bridge
# state, which only the laptop has. So LightVela signs up, the laptop mints.
#
#   lv_zai_farm.sh                     farm 3 accounts, ingest them here
#   LV_FARM_MAX=5 lv_zai_farm.sh       farm 5
#   LV_FARM_HOST=box LV_FARM_DOMAIN=.. override host / mail domain
#
# Ingested accounts are appended to ~/.zcode-bridge/accounts.txt with a bridge
# key; the zcode watcher then mints each one and injects it into 9Router (it
# auto-mints any account without a JWT), so this script does no minting itself.
set -u
H=${LV_FARM_HOST:-lightvela}
N=${LV_FARM_MAX:-3}
D=${LV_FARM_DOMAIN:-kancalabs.my.id}     # keyless Tempik domain -- no secrets on the box
REPO=$HOME/Auto-FreeCF
PY=${LV_PY:-$HOME/.local/share/auto-freecf/camoufox-venv/bin/python}
LOCAL_TMP=/tmp/lv_zai

echo "[zai-farm] host=$H n=$N domain=$D  $(date '+%F %T')"

# 1. sign up N accounts on the remote box (fresh scripts, xvfb, masked egress).
#    zai_signup exits non-zero on failure; keep going so one bad run is not fatal.
ssh -o BatchMode=yes -o ConnectTimeout=20 "$H" \
  "cd ~/Auto-FreeCF && git pull -q 2>/dev/null; mkdir -p /tmp/lv_zai; rm -f /tmp/lv_zai/*.json; \
   for i in \$(seq 1 $N); do \
     echo \"--- remote signup \$i/$N ---\"; \
     ZAI_MAIL_DOMAIN=$D xvfb-run -a ~/venv/bin/python scripts/zai_signup.py \
       --attempts 2 --out-json /tmp/lv_zai/\$i.json 2>&1 | tail -6; \
   done" 2>&1 | tail -40

# 2. pull the records home
mkdir -p "$LOCAL_TMP"; rm -f "$LOCAL_TMP"/*.json
scp -q "$H:/tmp/lv_zai/*.json" "$LOCAL_TMP/" 2>/dev/null || true
n=$(ls "$LOCAL_TMP"/*.json 2>/dev/null | wc -l)
echo "[zai-farm] pulled $n record(s)"
[ "$n" -gt 0 ] || { echo "[zai-farm] nothing to ingest"; exit 0; }

# 3. ingest: add each ok record to the bridge (accounts.txt + a key). The watcher
#    mints + injects; nothing here touches 9Router directly.
"$PY" - "$LOCAL_TMP" "$REPO" <<'PY'
import json, pathlib, subprocess, sys, os
tmp, repo = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
bridge = pathlib.Path.home() / ".zcode-bridge"
known = set()
acc = bridge / "accounts.txt"
if acc.exists():
    known = {l.split(":", 1)[0] for l in acc.read_text().splitlines() if ":" in l}
ok = 0
for f in sorted(tmp.glob("*.json")):
    try:
        r = json.loads(f.read_text())
    except Exception:
        continue
    email, pw, st = r.get("email"), r.get("password"), r.get("status")
    if st != "ok" or not email or not pw:
        print("  - skip %s (%s)" % (email, st)); continue
    if email in known:
        print("  = have %s" % email); continue
    rc = subprocess.call([sys.executable, str(repo / "scripts" / "zcode.py"),
                          "account", "add", email, pw, "--no-mint"],
                         cwd=str(repo))
    if rc == 0:
        known.add(email); ok += 1
        print("  + %s" % email)
print("[zai-farm] ingested %d new account(s)" % ok)
PY

echo "[zai-farm] done $(date '+%F %T') -- watcher will mint + inject"
