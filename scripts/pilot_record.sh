#!/usr/bin/env bash
# Comparison pilot recorder (terminal B): lerobot-record over the remote receiver, LEFT arm, chest + left
# wrist D405, the SAME settings for both methods; the dataset name carries the method (local only).
# Usage: bash scripts/pilot_record.sh leader|pico [num_episodes]
# THE FOLLOWER MOVES BY ITSELF to the start pose when this starts. Keys: right arrow = next episode,
# a = return to the start pose, left arrow = redo last, esc = end.
METHOD="${1:?usage: pilot_record.sh leader|pico [num_episodes]}"
case "$METHOD" in leader|pico) ;; *) echo "method must be leader or pico" >&2; exit 1 ;; esac
cd ~/projects/openarm-orboh && REMOTE=true SIDE=left WRIST=true WRIST_SERIAL=409122272149 \
  OPERATOR="${OPERATOR:-kota}" SCENE=none NUM_EPISODES="${2:-5}" EPISODE_TIME_S="${EPISODE_TIME_S:-30}" \
  DISPLAY_DATA=false REPO_ID="Orboh/openarm_pilot_${METHOD}_tmp" \
  TASK="${TASK:-Pick up the object and place it in the bin}" \
  bash scripts/record_pick_place_openarm.sh "pilot-${METHOD}"
