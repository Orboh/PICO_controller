#!/usr/bin/env bash
# Wrist dry run (no arm, nothing sent): bash scripts/wrist_dry_run.sh left|right   (log-026 step 0)
SIDE="${1:?usage: wrist_dry_run.sh left|right}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" && exec bash scripts/pico_env.sh python -m pico_teleop.sender --dry-run --arms "$SIDE" --orientation \
  --log "runs/ori-dry-$SIDE-$(date +%Y%m%d-%H%M%S).jsonl"
