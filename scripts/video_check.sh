#!/usr/bin/env bash
# PICO video check WITHOUT the arm (log-031): dry-run sender + video toggle. Hold LEFT Y 1 s = on/off.
# Usage: bash scripts/video_check.sh [/dev/videoN]   (default /dev/video4 = chest D435i RGB on this PC)
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" && exec bash scripts/pico_env.sh python -m pico_teleop.sender --dry-run --arms left \
  --video-device "${1:-/dev/video4}" --log "runs/video-check-$(date +%Y%m%d-%H%M%S).jsonl"
