#!/usr/bin/env bash
# Run a command with the PICO SDK importable (xrobotoolkit_sdk, py3.10 build) and this repo's venv.
# Usage (from anywhere): bash scripts/pico_env.sh python -m pico_teleop.diag_input
# Refuses to start when the PICO PC Service is not listening (README.md "Gate 1", step 1).
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SDK_PY="${SDK_PY:-$HOME/Desktop/XRoboToolkit-PC-Service-Pybind/build/lib.linux-x86_64-cpython-310}"
SDK_LIB="${SDK_LIB:-/opt/apps/roboticsservice/SDK/x64}"
[ -x "$REPO/.venv/bin/python" ] || { echo "[pico_env] missing $REPO/.venv (README.md: Setup)" >&2; exit 1; }
[ -d "$SDK_PY" ] || { echo "[pico_env] missing SDK build $SDK_PY (set SDK_PY=...)" >&2; exit 1; }
if ! ss -tln 2>/dev/null | grep -qE ':60061( |$)'; then
  echo "[pico_env] PICO PC Service is not listening on 127.0.0.1:60061. Start it (README.md Gate 1 step 1)." >&2
  exit 1
fi
export PYTHONPATH="$REPO:$SDK_PY"   # replaces PYTHONPATH on purpose: ROS humble's path breaks the venv
export LD_LIBRARY_PATH="$SDK_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PATH="$REPO/.venv/bin:$PATH"
cd "$REPO" && exec "$@"
