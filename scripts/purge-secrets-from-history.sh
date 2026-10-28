#!/usr/bin/env bash
# Remove credential-bearing files from EVERY commit, then show what changed.
#
# Run this ONLY after you have rotated the leaked keys — rewriting history does
# not un-publish anything that was already pushed or cloned.
#
#   bash scripts/purge-secrets-from-history.sh --dry-run   # preview
#   bash scripts/purge-secrets-from-history.sh             # rewrite
#
# Requires git-filter-repo:  pip install git-filter-repo
#
# After it finishes you must force-push every branch you own:
#   git push --force --all fork && git push --force --all origin
# (never push to `upstream` — that is someone else's repository)
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

if ! command -v git-filter-repo >/dev/null 2>&1; then
  echo "✗ git-filter-repo not found. Install it first:" >&2
  echo "    pip install git-filter-repo" >&2
  exit 1
fi

# Paths that must disappear from history. Add new ones here as you find them.
PATHS=(
  "mail-adapter/config.example.json"   # had a live TMK_KEY
  "mail-adapter/adapter.py"            # had the same key as a fallback default
  "test_accounts.json"
  "tests/test_account.txt"
  "tests/test_accounts.txt"
  "tests/test_single.txt"
  "tests/test_batch.sh"
  "adb/cf_adb_creator.py"
)

echo "Repository : $REPO"
echo "Paths      : ${#PATHS[@]}"
printf '  - %s\n' "${PATHS[@]}"
echo

if [ "$DRY_RUN" = "1" ]; then
  echo "Dry run — commits that would be rewritten:"
  git log --oneline --all -- "${PATHS[@]}" | head -40
  echo
  echo "Re-run without --dry-run to rewrite."
  exit 0
fi

echo "Creating a safety backup branch…"
git branch -f backup/pre-purge-"$(date +%Y%m%d-%H%M%S)" HEAD

echo "Rewriting history (all refs)…"
git filter-repo --invert-paths --path-glob '' --force \
  $(printf -- '--path %q ' "${PATHS[@]}")

echo
echo "✓ History rewritten."
echo "  Re-add your remotes if filter-repo removed them:"
echo "    git remote add origin git@github.com:Ini-Amin/Kanca-Hub.git"
echo "    git remote add fork   https://github.com/Ini-Amin/Auto-FreeCF.git"
echo
echo "  Then force-push the branches YOU own (never upstream):"
echo "    git push --force --all fork"
echo "    git push --force --all origin"
echo
echo "  Finally: rotate every key that was in those files. History rewriting"
echo "  does not invalidate a credential that has already been copied."
