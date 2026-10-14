#!/usr/bin/env bash
# Capture the live X display (the automation browser) to a PNG.
# Usage: shot.sh [output.png] [display]
OUT="${1:-$HOME/.config/auto-freecf/logs/shot.png}"
DISP="${2:-:0}"
DISPLAY="$DISP" scrot "$OUT" && echo "$OUT"
