#!/usr/bin/env bash
# Quick live check of the PICO input (reads only, sends nothing): rate, left/right pose status, facing.
# Usage: bash scripts/check_input.sh        (headset on, sending on, facing the robot's forward)
REPO="$(cd "$(dirname "$0")/.." && pwd)"
exec bash "$REPO/scripts/pico_env.sh" python - <<'PY' 2>&1 | grep -E "^(rate|ERROR)"
import time
import numpy as np
from pico_teleop.xr_input import SdkSource, pose_status
from pico_teleop.sender import headset_facing_deg
try:
    s = SdkSource()
except RuntimeError as e:
    print("ERROR", e)
    raise SystemExit(1)
t, n, ys = time.monotonic(), set(), []
while time.monotonic() - t < 1.5:
    f = s.read()
    n.add(f.t_device_ns)
    y = headset_facing_deg(f.headset)
    if y is not None:
        ys.append(y)
    time.sleep(0.005)
s.close()
fac = round(float(np.median(ys))) if ys else None
print(f"rate ~{len(n - {0}) / 1.5:.0f} Hz | L {pose_status(f.left.pose)} | R {pose_status(f.right.pose)} | facing {fac} deg"
      + ("  (OK)" if fac is not None and abs(fac) <= 15 else "  (turn to face the robot's forward / recenter)"))
PY
