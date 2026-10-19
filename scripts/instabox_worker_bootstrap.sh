#!/bin/sh
# Bootstrap an InstaBox machine as a bounty worker.
# InstaBox SDK writes the repo into HOME (/data/home, persistent) then runs:
#   sbx.commands.run('sh instabox_worker_bootstrap.sh')
# Needs: TAILSCALE_AUTH_KEY (env), BLU_TS_IP (env, blu's tailnet IP).
# The worker treats blu as the hub: 9router for models, gigs_api for jobs.
set -e
cd "$HOME"
export DEBIAN_FRONTEND=noninteractive

# 1. deps (python3.12 preferred; 3.13 also fine for our pinned wheels)
apt-get update -qq >/dev/null 2>&1 || true
apt-get install -y -qq python3-venv python3-pip git jq curl openssh-client >/dev/null 2>&1 || true

# 2. repo (public, no creds needed)
[ -d Auto-FreeCF ] || git clone -q --depth 1 https://github.com/Ini-Amin/Kanca-Hub.git Auto-FreeCF
# ponytail: if the repo goes private again, tar-pipe scripts/ from blu like
# rail_zai_farm.sh sync_lightvela_code does
cd Auto-FreeCF

# 3. venv + deps (requirements.txt covers the worker path; camoufox not needed
# for hunting, only for farming)
python3 -m venv ~/.venv
~/.venv/bin/pip install -q -r requirements.txt 2>/dev/null || ~/.venv/bin/pip install -q httpx playwright pytest

# 4. tailscale join -> reach blu:20128 (9router) and blu:8795 (gigs_api)
if [ -n "$TAILSCALE_AUTH_KEY" ]; then
  curl -fsSL https://tailscale.com/install.sh | sh >/dev/null 2>&1
  tailscale up --authkey "$TAILSCALE_AUTH_KEY" --hostname "instabox-worker" --accept-routes=false >/dev/null 2>&1 || true
fi

# 5. worker env: blu's 9router is the model gateway (OPENAI_BASE_URL is what
# agent.py:1724 reads). The API key comes from SDK envs at runtime, never in the repo.
cat > ~/.worker_env <<EOF
export OPENAI_BASE_URL="http://${BLU_TS_IP:-100.120.116.62}:20128/v1"
export NINE_ROUTER_URL="http://${BLU_TS_IP:-100.120.116.62}:20128"
export GIGS_API_URL="http://${BLU_TS_IP:-100.120.116.62}:8795"
EOF
echo "env written: ~/.worker_env (set OPENAI_API_KEY before hunting)"

# 5. health: blu's 9router must answer before we call the worker ready
for i in 1 2 3 4 5; do
  curl -sf -m 5 "http://${BLU_TS_IP:-100.120.116.62}:20128/api/health" && break
  sleep 3
done
echo "bootstrap ok: venv=$HOME/.venv router=http://${BLU_TS_IP:-100.120.116.62}:20128"
echo "smoke: ~/.venv/bin/python scripts/abh_hunt.py --help"
