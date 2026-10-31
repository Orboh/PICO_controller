#!/usr/bin/env bash
# Terminal B for the recording dry run (gate 4): lerobot-record with the PICO stream (REMOTE, left arm,
# chest + left wrist D405), 2 x 15 s episodes into a local test dataset (never pushed).
# THE ARM MOVES BY ITSELF to the start pose when this starts. Keys here: right arrow = next episode,
# a = return to start pose, left arrow = redo last, esc = end.
cd ~/projects/openarm-orboh && REMOTE=true SIDE=left WRIST=true WRIST_SERIAL=409122272149 \
  OPERATOR="${OPERATOR:-kota}" SCENE=none NUM_EPISODES="${NUM_EPISODES:-2}" EPISODE_TIME_S="${EPISODE_TIME_S:-15}" \
  DISPLAY_DATA=false REPO_ID=Orboh/openarm_pico_gate4_tmp TASK="PICO gate 4 dry run" \
  bash scripts/record_pick_place_openarm.sh "${1:-pico-gate4-dry}"
