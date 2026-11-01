#!/usr/bin/env bash
# Leader-arm sender for one arm (log-038; generalises pilot_leader_sender.sh): the leader agent of openarm-orboh.
# Usage: bash scripts/leader_sender.sh left|right [gain]. Ctrl-C makes the LEADER arm go limp: hold it first.
SIDE="${1:?usage: leader_sender.sh left|right [gain]}"
case "$SIDE" in left|right) ;; *) echo "side must be left or right" >&2; exit 1;; esac
THOR_IP=127.0.0.1 exec bash ~/projects/openarm-orboh/scripts/run_leader_agent_${SIDE}.sh "${2:-1.0}"
