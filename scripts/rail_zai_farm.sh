#!/bin/bash
# Farm z.ai accounts on throwaway Railway boxes, pull the records home, mirror
# them to LightVela (the always-on store the bridge can read from).
#
#   scripts/rail_zai_farm.sh [count]      count = boxes to use, default 1
#
# Per box: fresh Railway egress IP -> repo push -> bootstrap -> install the
# camoufox browser from a direct release asset (api.github.com is rate-limited
# there) -> z.ai signup -> pull the record -> wipe the box.
#
# Secrets stay off the box: ZAI_MAIL_DOMAIN=kancalabs.my.id selects the keyless
# Tempik inbox, so the relay mail key never leaves the laptop.
set -u
cd "$(dirname "$0")/.."

COUNT=${1:-1}
LEDGER="$HOME/.config/auto-freecf/zai_accounts.jsonl"
KEYDIR="$HOME/.config/auto-freecf/railbox"
RB=(python3 scripts/railbox.py)
SCP=(scp -q -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o LogLevel=ERROR)
mkdir -p "$(dirname "$LEDGER")" && touch "$LEDGER"

merge() {  # merge <pulled.json> : append the record if ok and its email is new
    python3 - "$1" "$LEDGER" <<'PY'
import json, sys
src, ledger = sys.argv[1], sys.argv[2]
try:
    rec = json.load(open(src))
except Exception:
    print("  pull: no usable record"); sys.exit(0)
if rec.get("status") != "ok":
    print(f"  record: {rec.get('status') or 'no status'} (not stored)"); sys.exit(0)
seen = set()
for line in open(ledger, encoding="utf-8", errors="replace"):
    try:
        seen.add(json.loads(line).get("email"))
    except Exception:
        pass
if rec.get("email") in seen:
    print(f"  record: {rec['email']} already stored"); sys.exit(0)
with open(ledger, "a", encoding="utf-8") as f:
    f.write(json.dumps(rec) + "\n")
print(f"  stored: {rec['email']}")
PY
}

for i in $(seq 1 "$COUNT"); do
    echo "=== box $i/$COUNT ==="
    NAME=$("${RB[@]}" new | sed -n 's/^box \([^:]*\):.*/\1/p')
    if [ -z "${NAME:-}" ]; then echo "no box created; stopping" >&2; exit 1; fi

    wipe() { "${RB[@]}" wipe --name "$NAME" >/dev/null 2>&1 || true; }
    trap wipe EXIT

    "${RB[@]}" push --name "$NAME" || { echo "push failed"; exit 1; }
    "${RB[@]}" ssh --name "$NAME" "sh /app/work/scripts/railbox_bootstrap.sh" 2>&1 | tail -1
    "${RB[@]}" ssh --name "$NAME" "sh /app/work/scripts/rail_install_camoufox.sh" 2>&1 | tail -1
    "${RB[@]}" ssh --name "$NAME" \
        "cd /app/work && ZAI_MAIL_DOMAIN=kancalabs.my.id xvfb-run -a /app/venv/bin/python scripts/zai_signup.py --attempts 3 --out-json /app/work/zai_out.json" 2>&1 | tail -20

    OUT=$(mktemp)
    if "${SCP[@]}" -i "$KEYDIR/$NAME/key" railway.new:/app/work/zai_out.json "$OUT" 2>/dev/null; then
        merge "$OUT"
    else
        echo "  pull failed"
    fi
    rm -f "$OUT"

    # best-effort mirror: a mirror failure must not lose the local records
    ssh -o ConnectTimeout=15 lightvela 'mkdir -p ~/.config/auto-freecf' 2>/dev/null \
        && scp -q "$LEDGER" lightvela:~/.config/auto-freecf/zai_accounts.jsonl 2>/dev/null \
        && echo "  mirrored to lightvela" || echo "  mirror to lightvela failed (ignored)"

    wipe; trap - EXIT
done

echo "ledger: $LEDGER ($(wc -l < "$LEDGER") record(s))"