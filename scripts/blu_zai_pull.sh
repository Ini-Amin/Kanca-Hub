#!/usr/bin/env bash
# Pull farmed z.ai records from the farm box into THIS box's bridge + 9router.
#
# RUN ON blu. blu owns the 9router sqlite DB, so minting/injection happens
# here; the farm box only farms and holds its ledger. Idempotent: duplicate
# emails are skipped by merge() and ingest_bridge, so running this on a timer
# is safe. Wired as blu-zai-pull.timer after the dash-password ingest fix
# (cd41300) — before that, 3 of 66 lightvela records were silently dropped.
set -u
FARM=${ZAI_FARM_HOST:-lightvela}
LEDGER="$HOME/.config/auto-freecf/zai_accounts.jsonl"
REPO="${R9_FARM_REPO:-${HOME:-/root}/Auto-FreeCF}"
mkdir -p "$(dirname "$LEDGER")"

echo "[zai-pull] pulling ledger from $FARM $(date '+%F %T')"
ssh -o BatchMode=yes -o ConnectTimeout=20 "$FARM" "cat ~/.config/auto-freecf/zai_accounts.jsonl" \
  > "$LEDGER.remote" 2>/dev/null || { echo "[zai-pull] pull failed"; exit 0; }

python3 - "$LEDGER.remote" "$LEDGER" <<'PY'
import json, sys
src, ledger = sys.argv[1], sys.argv[2]
seen = set()
try:
    for line in open(ledger, encoding="utf-8", errors="replace"):
        try: seen.add(json.loads(line).get("email"))
        except Exception: pass
except FileNotFoundError:
    pass
new = 0
with open(ledger, "a", encoding="utf-8") as out:
    for line in open(src, encoding="utf-8", errors="replace"):
        try: rec = json.loads(line)
        except Exception: continue
        if rec.get("email") in seen: continue
        out.write(json.dumps(rec) + "\n"); seen.add(rec.get("email")); new += 1
print(f"[zai-pull] merged {new} new record(s)")
PY

# ingest ledger -> bridge accounts.txt (the fixed, dash-password-safe path)
python3 - "$LEDGER" "$REPO" <<'PY'
import json, pathlib, subprocess, sys
ledger, repo = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
acc = pathlib.Path.home() / ".zcode-bridge/accounts.txt"
known = {l.split(":",1)[0] for l in acc.read_text().splitlines() if ":" in l} if acc.exists() else set()
ok = 0
for line in ledger.read_text(errors="replace").splitlines():
    try: r = json.loads(line)
    except Exception: continue
    e, p, st = r.get("email"), r.get("password"), r.get("status")
    if st != "ok" or not e or not p or e in known: continue
    rc = subprocess.call(["python3", "scripts/zcode.py", "account", "add", "--no-mint", e, "--", p],
                         cwd=str(repo), stdout=subprocess.DEVNULL)
    if rc == 0: known.add(e); ok += 1
print(f"[zai-pull] ingested {ok} new account(s) into the bridge")
PY
python3 scripts/zcode.py watch --once 2>&1 | tail -2
python3 scripts/inject_thk_9router.py --kind zcode 2>&1 | tail -1
python3 - <<'PY'
import sqlite3, os
db = sqlite3.connect(os.path.expanduser("~/.9router/db/data.sqlite"))
n = db.execute('select count(*) from providerConnections where name like "Zcode%"').fetchone()[0]
print(f"[zai-pull] 9router now serves {n} zcode connections")
PY
rm -f "$LEDGER.remote"
