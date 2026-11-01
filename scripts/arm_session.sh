#!/usr/bin/env bash
# Supervised arm session (no recorder) for ONE arm: R1 position checks or W1/R2 wrist checks (log-026).
# Usage: bash scripts/arm_session.sh left|right position|wrist [tag]
# Follower (another terminal, a person starts it): openarm-orboh scripts/run_teleop_remote_<side>.sh
set -euo pipefail
SIDE="${1:?usage: arm_session.sh left|right position|wrist [tag]}"; MODE="${2:?position or wrist}"
case "$SIDE" in left|right) ;; *) echo "side must be left or right" >&2; exit 1;; esac
case "$MODE" in position) ORI=();; wrist) ORI=(--orientation);; *) echo "mode must be position or wrist" >&2; exit 1;; esac
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TAG="${3:-$MODE-$SIDE-$(date +%Y%m%d-%H%M%S)}"
cd "$REPO" && bash scripts/pico_env.sh python -m pico_teleop.sender --host 127.0.0.1 --arms "$SIDE" "${ORI[@]}" \
  --confirm-follower-at-start-pose --max-facing-deg "${MAX_FACING_DEG:-75}" \
  --log "runs/$TAG-sender.jsonl" 2>&1 \
  | tee "runs/$TAG-sender.out" | grep --line-buffered -E "^\[sender\]|ENGAGED|released|FAULT|LOCKED|LINK|STOPPED|refused|Error|^\[(left |right)\]"
