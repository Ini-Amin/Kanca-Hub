#!/bin/sh
# Pull the durable bounty-pipeline state OFF blu, which is ephemeral: a Manus
# rebuild hands back a fresh box, so anything not pulled here is gone.
#
# Run this ON asus.  It is the other half of blu_bootstrap.sh.
#
#   scripts/blu_backup_pull.sh                 # asus -> blu
#   BOX=other scripts/blu_backup_pull.sh
#   DEST=/mnt/data/blu scripts/blu_backup_pull.sh
#
# WHY THIS SCRIPT EXISTS AT ALL
# -----------------------------
# The pipeline spec promised an asus-side pull-backup into ~/.blu-backup/ and
# there was nothing behind it: no script, no cron entry, and no rsync on blu.
# A backup that is documented but not implemented is worse than none, because
# it is believed.
#
# WHAT IT TAKES, AND WHY NOT rsync
# --------------------------------
# blu has no rsync and installing packages on a box that gets thrown away is
# how a backup stops running, so this is tar over ssh. blu is reached over the
# tailnet; the pull runs from asus, not the other way round, so the throwaway
# box holds no credentials for the durable one.
#
# WHERE THE FINDINGS ACTUALLY ARE
# -------------------------------
# The spec asked to back up `recon/*/findings/`. That path does not exist on
# blu — checking is what this was written after. A hunt records its findings in
# two other places, and both are in the list below:
#
#   recon/<target>/sessions/<run>/agent_session.json   the adjudicated
#                                                      `findings_log`, plus
#                                                      agent_trace.jsonl
#   recon/<target>/nuclei/findings.jsonl               raw scanner output
#
# `recon/*/findings/` is still listed, so this keeps working if ABH ever does
# create that layout, but it is NOT what is being protected today.
#
# VERIFICATION, NOT TRUST
# -----------------------
# A glob that matches nothing still produces a successful tar and a zero-byte
# extract. That is the exact failure this script exists to prevent, so the pull
# is checked afterwards and a quiet backup is a failed backup.
set -eu

BOX=${BOX:-blu}
DEST=${DEST:-$HOME/.blu-backup}
# Keep some history: the newest pull is always the live one, older ones are
# the rollback point when a rebuild goes wrong. pruned oldest-first.
KEEP=${KEEP:-5}

mkdir -p "$DEST"
# Pull into staging and only publish after the counts check out. Extracting
# straight into $DEST would mean a failed pull has already overwritten the last
# good one — the one thing this script is for.
STAMP=$(date +%Y%m%dT%H%M%S)
INCOMING="$DEST/.incoming-$STAMP"
mkdir -p "$INCOMING"
# Every exit path must drop the staging dir, not just the counts check below.
# `set -e` aborts on a broken tar stream before that check ever runs, and one
# leaked directory per failed pull is how a box nobody is watching fills up.
# Harmless on success: by then the tree has been moved out of INCOMING.
trap 'rm -rf "$INCOMING"' EXIT

echo "[backup] pulling from $BOX -> $DEST"

# Absolute paths, deliberately. This used to run `tar -C /` with paths relative
# to /, but the globs are expanded by the REMOTE shell, whose cwd is /root — so
# `recon/*/sessions` looked for /root/root/... , matched nothing, and was left
# as a literal that tar could not stat. With --ignore-failed-read the pull then
# "succeeded" carrying the two NON-glob paths and ZERO session records: a
# backup that reports ok and contains none of the findings. Absolute paths make
# the glob independent of the remote cwd. tar strips the leading / on its own.
ssh -o BatchMode=yes "$BOX" 'tar -cf - --ignore-failed-read \
    /root/Agentic-Bug-Hunter/bughunter/recon/*/sessions \
    /root/Agentic-Bug-Hunter/bughunter/recon/*/nuclei \
    /root/Agentic-Bug-Hunter/bughunter/recon/*/findings \
    /root/.config/auto-freecf/bounty \
    /root/Auto-FreeCF/patches 2>/dev/null' | tar -C "$INCOMING" -xf -

# ── verify ───────────────────────────────────────────────────────────────────
# Session records carry the findings; jobs are the tracked pipeline state.
# Both are counted here rather than assumed, and both have to be non-zero.
SESS=$(find "$INCOMING/root/Agentic-Bug-Hunter/bughunter/recon" \
        -name agent_session.json 2>/dev/null | wc -l)
JOBS=$(find "$INCOMING/root/.config/auto-freecf/bounty" \
        -maxdepth 2 -name job.json 2>/dev/null | wc -l)
NUC=$(find "$INCOMING/root/Agentic-Bug-Hunter/bughunter/recon" \
        -name findings.jsonl 2>/dev/null | wc -l)
PATCH=$(find "$INCOMING/root/Auto-FreeCF/patches" -name '*.patch' 2>/dev/null | wc -l)

printf '  %-12s %s\n' sessions "$SESS agent_session.json (findings_log + trace)"
printf '  %-12s %s\n' nuclei "$NUC findings.jsonl"
printf '  %-12s %s\n' jobs "$JOBS job.json"
printf '  %-12s %s\n' patches "$PATCH patch file(s)"

if [ "$SESS" -eq 0 ] || [ "$JOBS" -eq 0 ]; then
  echo "[backup] FAILED — nothing durable came across (sessions=$SESS jobs=$JOBS)" >&2
  echo "[backup] check that blu is reachable and the pipeline has run at least once" >&2
  # Leave the previous good tree exactly as it was — staging only, and the
  # EXIT trap below removes it.
  exit 1
fi

# ── publish + history ────────────────────────────────────────────────────────
# Snapshots, not an overwrite: a bad rebuild needs the state that preceded it.
# The previously live tree becomes a snapshot, staging becomes the live tree.
if [ -d "$DEST/root" ]; then
  mkdir -p "$DEST/snapshots/$STAMP"
  mv "$DEST/root" "$DEST/snapshots/$STAMP/root"
fi
mv "$INCOMING/root" "$DEST/root"
rmdir "$INCOMING" 2>/dev/null || true

COUNT=$(ls -1 "$DEST/snapshots" 2>/dev/null | wc -l)
while [ "$COUNT" -gt "$KEEP" ]; do
  OLDEST=$(ls -1 "$DEST/snapshots" 2>/dev/null | sort | head -1)
  rm -rf "$DEST/snapshots/$OLDEST"
  COUNT=$((COUNT - 1))
done

echo "[backup] OK  $STAMP  (kept $KEEP snapshots)"
echo "[backup] newest state is at $DEST/root"
