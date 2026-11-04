#!/usr/bin/env bash
# Supervised arm session (no recorder) for ONE arm: R1 position checks or W1/R2 wrist checks (log-026).
# Usage: bash scripts/arm_session.sh left|right|both position|wrist [tag]   (both = one sender for both arms; only one process may read the PICO)
# PREMOVE_CM=10 bash scripts/arm_session.sh left wrist  -> press Enter in this terminal: the arm moves 10 cm forward slowly (log-028)
# VIDEO=/dev/video4 bash scripts/arm_session.sh ...  -> hold LEFT Y 1 s to start/stop the PICO video (chest D435i RGB, log-031)
# Follower (another terminal, a person starts it): openarm-orboh scripts/run_teleop_remote_<side>.sh
# POSE_SET=<set> bash scripts/arm_session.sh ...  -> the sender uses that start-pose set instead of CURRENT
#   (give the receiver the same POSE_SET; for PICO → LEADER the receiver is scripts/pico_leader_receiver.sh, 2026-11-04)
set -euo pipefail
SIDE="${1:?usage: arm_session.sh left|right|both position|wrist [tag]}"; MODE="${2:?position or wrist}"
case "$SIDE" in left|right|both) ;; *) echo "side must be left, right or both" >&2; exit 1;; esac
case "$MODE" in position) ORI=();; wrist) ORI=(--orientation);; *) echo "mode must be position or wrist" >&2; exit 1;; esac
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TAG="${3:-$MODE-$SIDE-$(date +%Y%m%d-%H%M%S)}"
cd "$REPO" && bash scripts/pico_env.sh python -m pico_teleop.sender --host 127.0.0.1 --arms "$SIDE" "${ORI[@]}" \
  ${POSE_SET:+--pose-set "$POSE_SET"} --confirm-follower-at-start-pose --max-facing-deg "${MAX_FACING_DEG:-75}" --premove-forward-cm "${PREMOVE_CM:-0}" ${VIDEO:+--video-device "$VIDEO"} \
  --log "runs/$TAG-sender.jsonl" 2>&1 \
  | tee "runs/$TAG-sender.out" | grep --line-buffered -E "^\[sender\]|^\[video\]|WALL|pre-move|ENGAGED|released|FAULT|LOCKED|LINK|STOPPED|refused|Error|^\[(left |right)\]"
