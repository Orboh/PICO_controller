#!/usr/bin/env bash
# Comparison pilot, LEADER method (terminal A): the existing leader_agent for the LEFT leader arm, to
# this PC's receiver on port 5702. Never run together with the PICO sender (same port).
# THE LEADER ARM MOVES BY ITSELF to its start pose on start; Ctrl-C makes it go LIMP (hold it first).
THOR_IP=127.0.0.1 exec bash ~/projects/openarm-orboh/scripts/run_leader_agent_left.sh "${1:-1.0}"
