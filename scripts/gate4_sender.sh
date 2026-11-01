#!/usr/bin/env bash
# Terminal A for the recording dry run (gate 4): PICO sender for the LEFT arm, status on screen.
# HZ=60 ACC=300 (smoother, log-032/037); WRIST_MODE=1 turns roll/pitch/yaw on (--orientation, log-037)
# Waits for the follower; nothing moves until terminal B (gate4_record.sh) runs. Ctrl-C to stop.
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TAG="${1:-gate4-$(date +%Y%m%d-%H%M%S)}"
cd "$REPO" && bash scripts/pico_env.sh python -m pico_teleop.sender --host 127.0.0.1 --arms left \
  --confirm-follower-at-start-pose --max-link-pause-s 300 --max-facing-deg "${MAX_FACING_DEG:-75}" --hz "${HZ:-50}" ${ACC:+--max-joint-acc-dps2 "$ACC"} ${WRIST_MODE:+--orientation} \
  --log "runs/$TAG-sender.jsonl" 2>&1 \
  | tee "runs/$TAG-sender.out" | grep --line-buffered -E "^\[sender\]|ENGAGED|released|FAULT|LOCKED|LINK|STOPPED|refused|Error|\[left \]"
