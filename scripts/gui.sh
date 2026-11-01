#!/usr/bin/env bash
# Collection GUI (log-038): leader arm or PICO, one arm, same recorder. Open http://localhost:8090 on this PC.
# Usage: bash scripts/gui.sh [--port 8090]
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" && PYTHONPATH="$REPO" exec env -u AMENT_PREFIX_PATH "$REPO/.venv/bin/python" -m pico_teleop.gui.app "$@"
