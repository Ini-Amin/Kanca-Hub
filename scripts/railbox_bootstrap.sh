#!/bin/sh
# Run on a fresh Railway box: deps + headless browser. Usage: railbox.py ssh 'sh /app/work/scripts/railbox_bootstrap.sh'
set -e
cd /app/work
(apt-get install -y -qq libzstd-dev build-essential >/dev/null 2>&1 || true)
uv venv -q /app/venv
uv pip install -q --python /app/venv/bin/python -r requirements.txt camoufox playwright
/app/venv/bin/python -m camoufox fetch >/dev/null 2>&1 || true
/app/venv/bin/python -m patchright install chromium >/dev/null 2>&1 || true
echo "bootstrap ok: /app/venv/bin/python (use: xvfb-run -a /app/venv/bin/python scripts/<job>.py)"
