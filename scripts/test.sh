#!/usr/bin/env bash
# Offline tests. Clears PYTHONPATH (ROS humble's launch_testing plugin breaks pytest otherwise).
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" && env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/bin/python -m pytest "$@"
