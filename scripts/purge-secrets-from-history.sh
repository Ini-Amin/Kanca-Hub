#!/usr/bin/env bash
# Scrub leaked credentials from EVERY commit.
#
# Run this ONLY after rotating the keys — rewriting history does not
# un-publish anything that was already pushed or cloned.
#
#   bash scripts/purge-secrets-from-history.sh --dry-run   # preview
#   bash scripts/purge-secrets-from-history.sh             # rewrite
#
# Requires git-filter-repo:  pip install git-filter-repo
#
# Two passes, because the two cases need different treatment:
#   1. Files that exist ONLY to hold credentials  -> delete from all history
#   2. Files we keep (config example, adb tool)   -> replace the secret text
#
# A safety copy is written OUTSIDE the repo as a mirror clone, because a
# backup branch inside the repo would be rewritten by filter-repo too.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# Files that should not exist in any commit.
DELETE_PATHS=(
  "test_accounts.json"
  "tests/test_account.txt"
  "tests/test_accounts.txt"
  "tests/test_single.txt"
  "proxies.json"
  "proxy.json"
  "proxy_list.txt"
  "grokbot-shim/probes/last_auth_url.txt"
)

# Literal secrets are NOT stored here — a purge script that lists the secrets
# is itself a leak. Put them one per line in a local, git-ignored file.
# Lines may be plain literals, or use filter-repo's own prefixes:
#   tmk_abc123                       (matched literally)
#   regex:@old-domain\.com==>@example.com
#
#   mkdir -p ~/.config/auto-freecf
#   printf '%s\n' 'the-api-key' 'the-password' > ~/.config/auto-freecf/purge-secrets.txt
#   chmod 600 ~/.config/auto-freecf/purge-secrets.txt
SECRETS_FILE="${SECRETS_FILE:-$HOME/.config/auto-freecf/purge-secrets.txt}"

SECRETS=()
if [ -f "$SECRETS_FILE" ]; then
  while IFS= read -r line; do
    [ -n "$line" ] && SECRETS+=("$line")
  done < "$SECRETS_FILE"
fi

if [ "${1:-}" = "--dry-run" ]; then
  echo "Repository     : $REPO"
  echo "Delete paths   : ${#DELETE_PATHS[@]}"
  printf '  - %s\n' "${DELETE_PATHS[@]}"
  echo "Secret list    : ${#SECRETS[@]} entries from $SECRETS_FILE"
  [ "${#SECRETS[@]}" -eq 0 ] && echo "                 (missing — pass 2 would be skipped)"
  echo
  echo "Commits touching deleted paths:"
  git log --oneline --all -- "${DELETE_PATHS[@]}" | head -20
  echo
  if [ "${#SECRETS[@]}" -gt 0 ]; then
    echo "Commits containing the first listed secret:"
    git log --oneline --all -S"${SECRETS[0]}" | head -10
    echo
  fi
  echo "Re-run without --dry-run to rewrite (needs git-filter-repo)."
  exit 0
fi

if [ "${#SECRETS[@]}" -eq 0 ]; then
  echo "✗ Secret list not found or empty: $SECRETS_FILE" >&2
  echo "  Create it with one secret per line (see the header of this script)." >&2
  exit 1
fi

if ! command -v git-filter-repo >/dev/null 2>&1; then
  echo "✗ git-filter-repo not found. Install it first:" >&2
  echo "    pip install git-filter-repo" >&2
  exit 1
fi

# ── Safety copy outside the repo ────────────────────────────────────────────
BACKUP="${BACKUP:-$HOME/Auto-FreeCF-pre-purge-$(date +%Y%m%d-%H%M%S).git}"
echo "Safety copy: $BACKUP"
git clone --mirror --quiet "$REPO" "$BACKUP"
echo "  ($(du -sh "$BACKUP" | cut -f1), all refs preserved)"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Build the replacement expression file OUTSIDE the repo.
: > "$WORK/replacements.txt"
for s in "${SECRETS[@]}"; do
  case "$s" in
    literal:*|regex:*|glob:*) printf '%s==>REDACTED\n' "$s" >> "$WORK/replacements.txt" ;;
    *)                        printf 'literal:%s==>REDACTED\n' "$s" >> "$WORK/replacements.txt" ;;
  esac
done

# ── Pass 1: delete credential-only files ────────────────────────────────────
DELETE_ARGS=()
for p in "${DELETE_PATHS[@]}"; do DELETE_ARGS+=(--path "$p"); done

echo
echo "Pass 1/2 — deleting credential-only files from all history…"
git filter-repo --force --invert-paths "${DELETE_ARGS[@]}"

# ── Pass 2: replace secret text in the files that remain ────────────────────
echo
echo "Pass 2/2 — replacing secret text in the files that remain…"
git filter-repo --force --replace-text "$WORK/replacements.txt"

echo
echo "✓ History rewritten locally."
echo
echo "Verify:"
echo "    git log --all --oneline -- ${DELETE_PATHS[*]}    # must print nothing"
echo "    git log --all -S'<first secret>' --oneline       # must print nothing"
echo
echo "filter-repo drops remotes; re-add the ones you own:"
echo "    git remote add origin git@github.com:Ini-Amin/Kanca-Hub.git"
echo "    git remote add fork   https://github.com/Ini-Amin/Auto-FreeCF.git"
echo
echo "Then force-push the branches YOU own (never upstream):"
echo "    git push --force --all fork"
echo "    git push --force --all origin"
echo
echo "Safety copy: $BACKUP"
echo "Delete it once you are satisfied — it still contains the secrets."
echo "Rotate the keys anyway: copies already pushed still exist elsewhere."
