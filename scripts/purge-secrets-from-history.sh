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
# Afterwards, force-push only the remotes you own:
#   git push --force --all fork && git push --force --all origin
# Never force-push `upstream` — that repository is not yours.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# Files that should not exist in any commit.
DELETE_PATHS=(
  "test_accounts.json"
  "tests/test_account.txt"
  "tests/test_accounts.txt"
  "tests/test_single.txt"
)

# Literal secrets are NOT stored here — a purge script that lists the secrets
# is itself a leak. Put them one per line in a local, git-ignored file:
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
  echo "Secret list    : ${#SECRETS[@]} strings from $SECRETS_FILE"
  [ "${#SECRETS[@]}" -eq 0 ] && echo "                 (missing — pass 2 will be skipped)"
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

BACKUP="backup/pre-purge-$(date +%Y%m%d-%H%M%S)"
echo "Safety branch: $BACKUP"
git branch -f "$BACKUP" HEAD

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Build the replacement expression file OUTSIDE the repo: literal:->placeholder
: > "$WORK/replacements.txt"
for s in "${SECRETS[@]}"; do
  printf 'literal:%s==>REDACTED\n' "$s" >> "$WORK/replacements.txt"
done

echo
echo "Pass 1/2 — deleting credential-only files from all history…"
git filter-repo --force --invert-paths \
  $(printf -- '--path %q ' "${DELETE_PATHS[@]}")

echo
echo "Pass 2/2 — replacing secret text in the files that remain…"
git filter-repo --force --replace-text "$WORK/replacements.txt"

echo
echo "✓ History rewritten. Verify with:"
echo "    git log --all -S'${SECRETS[0]}' --oneline     # must print nothing"
echo "    git log --all -- ${DELETE_PATHS[*]}           # must print nothing"
echo
echo "  Remotes may have been dropped by filter-repo; re-add yours:"
echo "    git remote add origin git@github.com:Ini-Amin/Kanca-Hub.git"
echo "    git remote add fork   https://github.com/Ini-Amin/Auto-FreeCF.git"
echo
echo "  Then force-push the branches YOU own (never upstream):"
echo "    git push --force --all fork"
echo "    git push --force --all origin"
echo
echo "  Backup kept at branch: $BACKUP"
echo "  Rotate the keys anyway — copies already pushed still exist."
