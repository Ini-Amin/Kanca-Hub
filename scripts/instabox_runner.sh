#!/usr/bin/env bash
# scripts/instabox_runner.sh — Run one-shot build/task jobs on the instabox VM.
#
# Usage:
#   scripts/instabox_runner.sh run <command...>        # exec a command in a temp workdir, stream output back, clean up
#   scripts/instabox_runner.sh script <local.sh>       # upload+exec a local script in a temp workdir
#   scripts/instabox_runner.sh fetch <vm_path>         # pull a build artifact back to laptop (downloads/)
#   scripts/instabox_runner.sh status                  # quick VM health: disk/mem/toolchain
#
# Design:
#   - Every `run`/`script` call gets a fresh /tmp/ibrun.<pid> workdir on the VM.
#   - A trap on EXIT removes that workdir — no job residue on the VM disk.
#   - All stdout/stderr streams back to the laptop; nothing accumulates remotely.
#   - Artifacts you want to keep must be `fetch`ed (or printed) before the job ends.
set -euo pipefail

HOST="${IBOX_HOST:-ibssh_2ju6q5pcoseyj3cxz4t32mexwe@ssh.instabox.sh}"
PORT="${IBOX_PORT:-2222}"
SSH_OPTS=(-o StrictHostKeyChecking=no -o ConnectTimeout=12 -p "$PORT")
DL_DIR="${IBOX_DL:-$HOME/.config/auto-freecf/instabox-downloads}"

ssh_exec() { ssh "${SSH_OPTS[@]}" "$HOST" "$@"; }

cmd_status() {
  ssh_exec '
    echo "== disk =="; df -h /data | tail -1
    echo "== mem  =="; free -h | sed -n 2p
    echo "== load =="; uptime
    echo "== toolchain =="
    go version 2>/dev/null | head -1
    rustc --version 2>/dev/null | head -1
    node --version 2>/dev/null
    python3 --version 2>/dev/null
    echo "== build-toolchain sources =="
    ls /data/home/build-toolchain 2>/dev/null || echo "(none)"
  '
}

cmd_run() {
  local cmd_str="$*"
  ssh_exec "set -eu
    W=/tmp/ibrun.\$\$
    mkdir -p \"\$W\"
    trap 'rm -rf \"\$W\"' EXIT
    cd \"\$W\"
    echo \"[ibrun] workdir: \$W (cleaned on exit)\"
    $cmd_str
  "
}

cmd_script() {
  local local_script="$1"
  [[ -f "$local_script" ]] || { echo "no such script: $local_script" >&2; exit 1; }
  ssh_exec "set -eu
    W=/tmp/ibrun.\$\$
    mkdir -p \"\$W\"
    trap 'rm -rf \"\$W\"' EXIT
    cd \"\$W\"
    echo \"[ibrun] workdir: \$W (cleaned on exit)\"
    bash -s -- <<'IBEOF'
$(cat "$local_script")
IBEOF
  "
}

cmd_fetch() {
  local vm_path="$1"
  mkdir -p "$DL_DIR"
  scp "${SSH_OPTS[@]}" "$HOST:$vm_path" "$DL_DIR/"
  echo "[fetch] saved: $DL_DIR/$(basename "$vm_path")"
}

main() {
  [[ $# -ge 1 ]] || { sed -n '2,20p' "$0"; exit 1; }
  case "$1" in
    run)    shift; cmd_run "$@" ;;
    script) shift; cmd_script "${1:?usage: script <local.sh>}" ;;
    fetch)  shift; cmd_fetch "${1:?usage: fetch <vm_path>}" ;;
    status) cmd_status ;;
    *) echo "unknown subcommand: $1 (run|script|fetch|status)" >&2; exit 1 ;;
  esac
}

main "$@"
