#!/bin/bash
# Farm z.ai accounts on a remote box, pull the records home, ingest them into the
# ZCode bridge (accounts.txt + a bridge key). The zcode watcher then mints each
# account and injects it into 9Router, so this script does no minting itself.
#
#   rail_zai_farm.sh [count]                 # default host: Railway throwaway boxes
#   FARM_HOST=lightvela rail_zai_farm.sh 3   # LightVela (always-on, WARP egress)
#
# Railway needs NO claim and NO account: an unclaimed box lives 60 min, which is
# plenty for farming when you bootstrap ONCE and then sign up inside the window
# (that is what this script now does). `railbox.py rotate` swaps in a fresh box
# ~4 min before expiry, and `railbox.py daemon` keeps rotating forever.
#   FARM_PER_BOX=4   rotate after 4 accounts on a box (0 = farm until expiry)
#   FARM_MARGIN=4    minutes of headroom before a box is considered expired
#
# Two hosts, one flow (fresh masked egress -> z.ai signup -> pull record ->
# ingest). They differ only in how the box is reached:
#   * Railway (default): a throwaway VM per WINDOW, keyed by a fresh SSH identity.
#     The box's own masked Railway egress is what the signup sees.
#   * lightvela: the always-on box, reached over plain ssh, farms with xvfb.
#
# Secrets stay off the box: ZAI_MAIL_DOMAIN defaults to the keyless Tempik inbox
# (kancalabs.my.id), so the relay mail key never leaves the laptop.
set -u
cd "$(dirname "$0")/.."

COUNT=${1:-1}
HOST=${FARM_HOST:-railway}
DOMAIN=${FARM_DOMAIN:-kancalabs.my.id}
LEDGER="$HOME/.config/auto-freecf/zai_accounts.jsonl"
KEYDIR="$HOME/.config/auto-freecf/railbox"
REPO="$PWD"
PY=${FARM_PY:-$HOME/.local/share/auto-freecf/camoufox-venv/bin/python}
RB=(python3 scripts/railbox.py)
SCP=(scp -q -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o LogLevel=ERROR)
mkdir -p "$(dirname "$LEDGER")" && touch "$LEDGER"

# merge <pulled.json> : append the record to the ledger if ok and its email is new
merge() {
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

# ingest the ledger into the ZCode bridge: accounts.txt + a bridge key per account.
# Skips anything already present, so it is safe to run after every farm.
ingest_bridge() {
    "$PY" - "$LEDGER" "$REPO" <<'PY'
import json, pathlib, subprocess, sys
ledger, repo = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
bridge = pathlib.Path.home() / ".zcode-bridge"
acc = bridge / "accounts.txt"
known = {l.split(":", 1)[0] for l in acc.read_text().splitlines() if ":" in l} if acc.exists() else set()
ok = 0
for line in ledger.read_text(errors="replace").splitlines():
    try:
        r = json.loads(line)
    except Exception:
        continue
    email, pw, st = r.get("email"), r.get("password"), r.get("status")
    if st != "ok" or not email or not pw or email in known:
        continue
    rc = subprocess.call([sys.executable, str(repo / "scripts" / "zcode.py"),
                          "account", "add", email, pw, "--no-mint"], cwd=str(repo))
    if rc == 0:
        known.add(email); ok += 1
        print("  + bridge: %s" % email)
print("[farm] ingested %d new account(s) into the bridge" % ok)
PY
}

farm_railway() {
    # WINDOW mode (default): one box, bootstrap ONCE, then farm as many accounts
    # as fit in the 60-min build window. The per-account box+wipe dance spent
    # most of the window on apt/uv/camoufox installs; the box's own masked
    # Railway egress is stable, so reusing it is both cheaper and quieter.
    # FARM_PER_BOX=0 -> farm until the box is ~4 min from expiry (then rotate).
    local per=${FARM_PER_BOX:-0}
    local margin=${FARM_MARGIN:-4}
    echo "=== railway window: target $COUNT account(s), up to ${per:-auto}/box ==="
    NAME=$("${RB[@]}" new | sed -n 's/^box \([^:]*\):.*/\1/p')
    if [ -z "${NAME:-}" ]; then echo "no box created; stopping" >&2; exit 1; fi
    trap '"${RB[@]}" wipe --name "$NAME" >/dev/null 2>&1 || true' EXIT
    box_ready "$NAME" || { echo "box $NAME not ready; stopping" >&2; exit 1; }
    echo "--- box $NAME: farming inside the window ---"
    for i in $(seq 1 "$COUNT"); do
        # stop this box when it is nearly expired (a fresh box rotates in)
        left=$("${RB[@]}" status 2>/dev/null | awk -v n="$NAME" '$1==n {print $2}')
        if [ -n "${left:-}" ] && [ "$left" -le "$margin" ] 2>/dev/null; then
            echo "--- box $NAME has ${left} min left -> rotating ---"
            NAME=$(rotate_box "$margin") || break
        fi
        echo "=== signup $i/$COUNT on $NAME ==="
        "${RB[@]}" ssh --name "$NAME" \
            "cd /app/work && ZAI_MAIL_DOMAIN=$DOMAIN xvfb-run -a /app/venv/bin/python scripts/zai_signup.py --attempts 3 --out-json /app/work/zai_out.json" 2>&1 | tail -8
        OUT="$KEYDIR/pulled_$NAME.json"
        if "${SCP[@]}" -i "$KEYDIR/$NAME/key" railway.new:/app/work/zai_out.json "$OUT" 2>/dev/null; then
            merge "$OUT"
        else
            echo "  pull failed"
        fi
        rm -f "$OUT"
        if [ "${per:-0}" -gt 0 ] && [ "$i" -ge "$per" ] && [ "$i" -lt "$COUNT" ]; then
            echo "--- reached FARM_PER_BOX=$per -> rotating ---"
            NAME=$(rotate_box "$margin") || break
        fi
    done
    "${RB[@]}" wipe --name "$NAME" >/dev/null 2>&1 || true
    trap - EXIT
}

# box_ready NAME : push the repo, bootstrap deps, install camoufox. Idempotent.
box_ready() {
    local n=$1
    echo "--- box $n: push + bootstrap ---"
    "${RB[@]}" push --name "$n" || return 1
    "${RB[@]}" ssh --name "$n" "sh /app/work/scripts/railbox_bootstrap.sh" 2>&1 | tail -1
    "${RB[@]}" ssh --name "$n" "sh /app/work/scripts/rail_install_camoufox.sh" 2>&1 | tail -1
}

# rotate_box MARGIN : snapshot -> new box -> re-push -> bootstrap -> wipe old.
# Echoes the new box name. Returns non-zero if no box came up. NOTE: railbox.py's
# own rotate() only bootstraps, so we re-run box_ready() to get camoufox too.
rotate_box() {
    local margin=$1 n
    "${RB[@]}" rotate --margin "$margin" >/dev/null 2>&1 || true
    n=$("${RB[@]}" status 2>/dev/null | awk 'NR==1{print $1}')
    if [ -z "${n:-}" ]; then echo "rotate failed; stopping" >&2; return 1; fi
    echo "--- rotated to $n ---" >&2
    box_ready "$n" >&2 || return 1
    printf '%s' "$n"
}

farm_lightvela() {
    echo "=== $COUNT signup(s) on $HOST ==="
    # Transfer dir is persistent state, NOT /tmp: a reboot mid-run must not eat
    # pulled records (merge() only runs after the pull).
    local lvtmp="$HOME/.config/auto-freecf/lv_zai"
    ssh -o BatchMode=yes -o ConnectTimeout=20 "$HOST" \
      "cd ~/Auto-FreeCF && git pull -q 2>/dev/null; mkdir -p ~/.config/auto-freecf/lv_zai; rm -f ~/.config/auto-freecf/lv_zai/*.json; \
       for i in \$(seq 1 $COUNT); do \
         echo \"--- remote signup \$i/$COUNT ---\"; \
         ZAI_MAIL_DOMAIN=$DOMAIN xvfb-run -a ~/venv/bin/python scripts/zai_signup.py \
           --attempts 2 --out-json ~/.config/auto-freecf/lv_zai/\$i.json 2>&1 | tail -6; \
       done" 2>&1 | tail -40
    local tmp="$lvtmp"
    mkdir -p "$tmp"; rm -f "$tmp"/*.json
    scp -q "$HOST:.config/auto-freecf/lv_zai/*.json" "$tmp/" 2>/dev/null || true
    for f in "$tmp"/*.json; do [ -e "$f" ] && merge "$f"; done
}

case "$HOST" in
    railway) farm_railway ;;
    *)       farm_lightvela ;;
esac

ingest_bridge
echo "ledger: $LEDGER ($(wc -l < "$LEDGER") record(s))"
