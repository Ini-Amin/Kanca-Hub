#!/bin/bash
PASSWORD="REDACTED"

EMAILS=(
"REDACTED"
"REDACTED"
"REDACTED"
"REDACTED"
"REDACTED"
"REDACTED"
"REDACTED"
"REDACTED"
"REDACTED"
"REDACTED"
"REDACTED"
"REDACTED"
)

echo "Starting batch processing of ${#EMAILS[@]} accounts..."
echo ""

for email in "${EMAILS[@]}"; do
    echo "=========================================="
    echo "Processing: $email"
    echo "=========================================="
    ./run.sh --email "$email" --password "$PASSWORD"
    echo ""
    sleep 2
done

echo "=========================================="
echo "Batch processing complete!"
echo "Results in: /root/cf-account-bot/exports/cf_accounts.json"
echo "=========================================="
