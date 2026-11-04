#!/usr/bin/env bash
# Wrist dry run (no arm, nothing sent): [POSE_SET=<set>] bash scripts/wrist_dry_run.sh left|right [tag]   (log-026 step 0)
SIDE="${1:?usage: wrist_dry_run.sh left|right [tag]}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
# POSE_SET=<set> を付けるとその開始姿勢セットで（無ければ CURRENT）。出力は runs/<tag>.out にも残す（tcp_watch.py で読む）・2026-11-04 Suraj
TAG="${2:-ori-dry-$SIDE-$(date +%Y%m%d-%H%M%S)}"
cd "$REPO" && bash scripts/pico_env.sh python -m pico_teleop.sender --dry-run --arms "$SIDE" --orientation \
  ${POSE_SET:+--pose-set "$POSE_SET"} --log "runs/$TAG.jsonl" 2>&1 | tee "runs/$TAG.out"
