#!/usr/bin/env bash
# Fresh-install KancaHub on a new machine: venv + deps + shim + verify.
# Idempotent — safe to re-run. Run from anywhere:
#   bash ~/Auto-FreeCF/scripts/install.sh
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$HOME/.local/share/auto-freecf/venv"
SHIM="$HOME/.local/bin/kancahub"

say() { printf '  %-9s %s\n' "$1" "$2"; }

# 1. Python 3.10–3.13 (nodriver breaks on pre-release builds, e.g. 3.15rc)
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    if "$c" -c 'import sys; raise SystemExit(0 if (3,10) <= sys.version_info < (3,14) else 1)'; then
      PY="$c"
      break
    fi
  fi
done
if [ -z "$PY" ]; then
  echo "✗ No Python 3.10–3.13 found. Install one, then re-run." >&2
  exit 1
fi
say python "$PY ($($PY --version 2>&1))"

# 2. Managed venv (same path `moycf` uses, so both installers agree)
if [ ! -x "$VENV/bin/python" ]; then
  "$PY" -m venv "$VENV"
  say venv "created at $VENV"
else
  say venv "exists at $VENV"
fi

# 3. Deps: root requirements + signup flow requirements (nodriver lives there)
"$VENV/bin/pip" install -q -r "$REPO/requirements.txt"
say deps "root requirements installed"
if [ -f "$REPO/signup_from_scratch/requirements.txt" ]; then
  "$VENV/bin/pip" install -q -r "$REPO/signup_from_scratch/requirements.txt"
  say deps "signup requirements installed"
fi

# 4. Browser for the nodriver/patchright farms (skip if already fetched)
if [ ! -d "$HOME/.cache/ms-playwright" ]; then
  "$VENV/bin/python" -m patchright install chromium --only-shell >/dev/null 2>&1 || \
    "$VENV/bin/python" -m patchright install chromium >/dev/null 2>&1 || true
  say browser "chromium fetched (or already present)"
else
  say browser "already fetched"
fi

# 5. Shim on PATH
mkdir -p "$(dirname "$SHIM")"
cat > "$SHIM" <<EOF
#!/usr/bin/env bash
VENV_PY="\$HOME/.local/share/auto-freecf/venv/bin/python"
SCRIPT="\$HOME/Auto-FreeCF/scripts/kancahub.py"
if [ -x "\$VENV_PY" ]; then
  exec "\$VENV_PY" "\$SCRIPT" "\$@"
else
  exec python3 "\$SCRIPT" "\$@"
fi
EOF
chmod +x "$SHIM"
say shim "$SHIM"

# 6. Verify — doctor lists exactly what (if anything) is still missing
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "NOTE: add to PATH: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac
exec "$SHIM" doctor
