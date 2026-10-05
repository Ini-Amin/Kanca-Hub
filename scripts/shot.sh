#!/usr/bin/env bash
# Capture the live X display (the automation browser) to a PNG.
# Usage: shot.sh [output.png] [display]
OUT="${1:-/tmp/k12_shot.png}"
DISP="${2:-:0}"
DISPLAY="$DISP" scrot "$OUT" && echo "$OUT"
