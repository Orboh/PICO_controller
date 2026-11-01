"""Offline: replay real PICO input through the controller at <hz>, sample like lerobot-record (newest command
every 1/30 s), report smoothness. Usage: python jerkbench.py <frames.jsonl> <side> <t0_s> <t1_s>"""
import dataclasses, sys, json
import numpy as np
from pico_teleop.controller import TeleopConfig
from pico_teleop.kinematics import DEFAULT_TCP_OFFSET_M, DEFAULT_URDF
from pico_teleop.poses import load_start_pose
from pico_teleop.sender import ArmChannel, headset_facing_deg
from pico_teleop.xr_input import FreshnessTracker, XRFrame

path, side, T0, T1 = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
frames = [XRFrame.from_json(l) for l in open(path) if l.strip()]
th = np.array([f.t_host for f in frames]); th -= th[0]

def run(hz, acc, phase=0.0123):
    cfg = TeleopConfig(hz=hz, max_joint_acc_dps2=acc)
    ch = ArmChannel(side, load_start_pose(side), cfg, None, DEFAULT_URDF, 1.0, DEFAULT_TCP_OFFSET_M)
    tr = FreshnessTracker(stale_s=0.4)
    cmd_t, cmd_q, n = [], [], 0
    for k in range(int(T1 * hz)):
        t = k / hz
        while n + 1 < len(frames) and th[n + 1] <= t: n += 1
        fr = dataclasses.replace(frames[n], t_host=t)
        tr.update(fr)
        act, _ = ch.tick(fr.controller(side), tr.fresh(side), "ok/ok", t,
                         fr.t_device_ns / 1e9 if fr.t_device_ns else None, headset_facing_deg(fr.headset))
        if act is not None and t >= T0:
            cmd_t.append(t); cmd_q.append([act[f"joint_{i}.pos"] for i in range(1, 8)])
    cmd_t, cmd_q = np.array(cmd_t), np.array(cmd_q)
    ts = np.arange(T0 + phase, cmd_t[-1], 1 / 30)                 # recorder ticks
    idx = np.searchsorted(cmd_t, ts, side="right") - 1
    a = cmd_q[idx]
    v = np.abs(np.diff(a, axis=0)).max(1) * 30
    mv = v[2:] > 3
    jerk = np.abs(np.diff(a, n=3, axis=0)).sum(1) * 30 ** 3 / 1000
    st = np.linalg.norm(np.diff(a, axis=0), axis=1)
    m = st[1:] > 0.3
    r = st[1:][m] / np.maximum(st[:-1][m], 1e-6)
    dbl = np.mean((r > 1.5) | (r < 0.67))
    # lag: how far the command trails the IK answer is not visible here; use the command's delay vs 50Hz/no-acc
    return jerk[mv].mean(), 100 * dbl, a, ch.arm.n_wall_cycles

base = None
for hz, acc in [tuple(None if x == "None" else float(x) for x in c.split(":")) for c in sys.argv[5].split(",")]:
    j, d, a, w = run(hz, acc)
    if base is None: base = a
    nmin = min(len(a), len(base))
    diff = np.abs(a[:nmin] - base[:nmin]).max(1)
    print(f"hz {int(hz):3d} acc {str(acc):>5} deg/s2 | jerk {j:5.1f}k | step doubles {d:3.0f}% | vs today: median {np.median(diff):.2f} deg, 95% {np.percentile(diff,95):.2f} deg | wall cycles {w}")
