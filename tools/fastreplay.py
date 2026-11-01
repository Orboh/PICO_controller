"""Fast offline replay (no real time): counts FAULT / LOCKED / WALL and the closest joint-to-limit distance.
Usage: python fastreplay.py <frames.jsonl> <side> <orientation 0|1> [hz] [acc]"""
import dataclasses, sys
import numpy as np
from pico_teleop.controller import TeleopConfig
from pico_teleop.kinematics import DEFAULT_TCP_OFFSET_M, DEFAULT_URDF
from pico_teleop.poses import load_start_pose
from pico_teleop.sender import ArmChannel, headset_facing_deg
from pico_teleop.xr_input import FreshnessTracker, XRFrame
path, side, ori = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
hz = float(sys.argv[4]) if len(sys.argv) > 4 else 50.0
acc = float(sys.argv[5]) if len(sys.argv) > 5 else None
frames = [XRFrame.from_json(l) for l in open(path) if l.strip()]
th = np.array([f.t_host for f in frames]); th -= th[0]
ch = ArmChannel(side, load_start_pose(side), TeleopConfig(hz=hz, orientation=ori, max_joint_acc_dps2=acc),
                None, DEFAULT_URDF, 1.0, DEFAULT_TCP_OFFSET_M)
tr = FreshnessTracker(stale_s=0.4)
n, faults, walls, eng, lock_t, room = 0, [], 0, 0, None, 99.0
for k in range(int(th[-1] * hz)):
    t = k / hz
    while n + 1 < len(frames) and th[n + 1] <= t: n += 1
    fr = dataclasses.replace(frames[n], t_host=t); tr.update(fr)
    act, ev = ch.tick(fr.controller(side), tr.fresh(side), "ok/ok", t,
                      fr.t_device_ns / 1e9 if fr.t_device_ns else None, headset_facing_deg(fr.headset))
    if ev:
        if "FAULT:" in ev and "refused" not in ev: faults.append(ev.split("(")[0][7:60])
        if ev.startswith("WALL"): walls += 1
        if "ENGAGED: anchored" in ev: eng += 1
        if "LOCKED:" in ev and lock_t is None: lock_t = t
    q = ch.arm.q
    room = min(room, float(np.rad2deg(np.minimum(q - ch.arm.kin.lo, ch.arm.kin.hi - q)).min()))
print(f"{path.split('/')[-1][:30]:30} {side:5} {'wrist' if ori else 'pos  '} | engages {eng:3d} FAULT {len(faults):2d} "
      f"LOCKED {'at %.0f s' % lock_t if lock_t else 'no':9} WALL {walls:3d} ({ch.arm.n_wall_cycles / hz:.1f} s) | "
      f"min room {room:.1f} deg | of {th[-1]:.0f} s")
for f in faults: print("     FAULT:", f)
