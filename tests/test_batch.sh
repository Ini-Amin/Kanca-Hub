#!/bin/bash
# Batch runner for the signup pipeline.
#
# Accounts are read from a local, git-ignored file — never hardcode them here.
# Format: one "email:password" per line (see tests/test_accounts.txt.example).
#
# Usage:
#   ACCOUNTS_FILE=~/my-accounts.txt ./tests/test_batch.sh
set -eu

ACCOUNTS_FILE="${ACCOUNTS_FILE:-./tests/test_accounts.txt}"

if [ ! -f "$ACCOUNTS_FILE" ]; then
    echo "Account file not found: $ACCOUNTS_FILE" >&2
    echo "Copy tests/test_accounts.txt.example to a local path and fill it in." >&2
    exit 1
fi

echo "Starting batch processing from $ACCOUNTS_FILE..."
echo ""

while IFS=: read -r email password; do
    # Skip blanks and comments
    case "$email" in ""|\#*) continue ;; esac
    echo "=========================================="
    echo "Processing: $email"
    echo "=========================================="
    ./run.sh --email "$email" --password "$password"
    echo ""
    sleep 2
done < "$ACCOUNTS_FILE"

echo "=========================================="
echo "Batch processing complete!"
echo "Results in: ./exports/cf_accounts.json"
echo "=========================================="
