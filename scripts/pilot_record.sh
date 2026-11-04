#!/usr/bin/env bash
# Comparison pilot recorder (terminal B): lerobot-record over the remote receiver, LEFT arm, chest + left
# wrist D405, the SAME settings for both methods; the dataset name carries the method (local only).
# Usage: bash scripts/pilot_record.sh leader|pico [num_episodes]
# THE FOLLOWER MOVES BY ITSELF to the start pose when this starts. Keys: right arrow = next episode,
# a = return to the start pose, left arrow = redo last, esc = end.
METHOD="${1:?usage: pilot_record.sh leader|pico [num_episodes]}"
case "$METHOD" in leader|pico) ;; *) echo "method must be leader or pico" >&2; exit 1 ;; esac
SIDE="${SIDE:-left}"   # log-038: left or right (the GUI passes it)
case "$SIDE" in left) WS=409122272149;; right) WS=352122273417;; *) echo "SIDE must be left or right" >&2; exit 1;; esac
# 2026-11-04 Suraj: 保存を速く（動画を録画しながら書き出す・データの形は同じ）。openarm-orboh の STREAMING_ENCODING
# （branch suraj/feat-record-streaming-encoding・b690e87）が main に入ると効く。入るまでは openarm-orboh が無視する。
# STREAMING_ENCODING=false で従来どおり。
cd ~/projects/openarm-orboh && REMOTE=true SIDE="$SIDE" WRIST=true WRIST_SERIAL="${WRIST_SERIAL:-$WS}" \
  STREAMING_ENCODING="${STREAMING_ENCODING:-true}" ENCODER_THREADS="${ENCODER_THREADS:-2}" \
  OPERATOR="${OPERATOR:-kota}" SCENE="${SCENE:-none}" NUM_EPISODES="${2:-5}" EPISODE_TIME_S="${EPISODE_TIME_S:-30}" \
  DISPLAY_DATA=false REPO_ID="${REPO_ID:-Orboh/openarm_pilot_${METHOD}_tmp}" \
  TASK="${TASK:-Pick up the object and place it in the bin}" \
  env -u DISPLAY -u WAYLAND_DISPLAY bash scripts/record_pick_place_openarm.sh "${TAG:-pilot-${METHOD}}"   # keys from this terminal, as collect_bimanual.sh (log-042)
