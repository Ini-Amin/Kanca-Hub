#!/bin/sh
# Run on a fresh Railway box: deps + headless browser. Usage: railbox.py ssh 'sh /app/work/scripts/railbox_bootstrap.sh'
set -e
cd /app/work
apt-get update -qq >/dev/null 2>&1 || true
apt-get install -y -qq libzstd-dev build-essential libgtk-3-0 libdbus-glib-1-2 libasound2t64 libx11-xcb1 libxt6 libxtst6 libnss3 fonts-liberation >/dev/null 2>&1 || true
[ -x /app/venv/bin/python ] || uv venv -q --python 3.12 /app/venv   # box default is 3.15: too new for most wheels
uv pip install -q --python /app/venv/bin/python -r requirements.txt "camoufox[geoip]" playwright
/app/venv/bin/python -m camoufox fetch >/dev/null 2>&1 || true
/app/venv/bin/python -m patchright install chromium >/dev/null 2>&1 || true
echo "bootstrap ok: xvfb-run -a /app/venv/bin/python scripts/<job>.py"
