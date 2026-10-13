#!/bin/sh
# Run ON a fresh Railway box: install the camoufox browser without the GitHub
# API. The box's shared IP is rate-limited at api.github.com (camoufox's own
# fetcher always calls it), but direct release-asset URLs are not. So we derive
# the library's pinned build, download that exact asset, and write the metadata
# camoufox expects by hand.
#
#   sh /app/work/scripts/rail_install_camoufox.sh
set -e

D0=/root/.cache/camoufox
mkdir -p "$D0/browsers/official"
touch "$D0/.0.5_FLAG"

command -v unzip >/dev/null 2>&1 || {
    apt-get update -qq >/dev/null 2>&1 || true
    apt-get install -y -qq unzip >/dev/null 2>&1
}

PIN=$(/app/venv/bin/python - <<'PY'
from camoufox.browser_pin import effective_pin as p
x = p()
print(x.tag, x.version, x.build, x.repo)
PY
)
set -- $PIN
TAG=$1; VER=$2; BUILD=$3; REPO=$4
[ -n "$REPO" ] || { echo "no browser pin found" >&2; exit 1; }

ZIP=/tmp/camoufox.zip
[ -s "$ZIP" ] || curl -fsSL -o "$ZIP" \
    "https://github.com/$REPO/releases/download/$TAG/camoufox-$VER-$BUILD-lin.x86_64.zip"

SHA=$(sha256sum "$ZIP" | cut -d' ' -f1)
SHA8=$(printf '%s' "$SHA" | cut -c1-8)
DIR="$D0/browsers/official/$VER-$BUILD-$SHA8"
mkdir -p "$DIR"
[ -e "$DIR/camoufox" ] || unzip -q -o "$ZIP" -d "$DIR"

printf '{"version":"%s","build":"%s","prerelease":false,"asset_id":null,"asset_size":null,"asset_updated_at":null,"sha256":"%s","created_at":null}' \
    "$VER" "$BUILD" "$SHA" > "$DIR/version.json"
printf '{"active_version":"browsers/official/%s-%s-%s"}' "$VER" "$BUILD" "$SHA8" > "$D0/config.json"

# Fails loudly rather than letting the farm run browserless.
/app/venv/bin/python -c "from camoufox.pkgman import camoufox_path; print('camoufox:', camoufox_path())"