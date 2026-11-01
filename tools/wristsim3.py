import sys; sys.path.insert(0, "tests")
import numpy as np
from test_orientation import _c, ADAPT
from pico_teleop.controller import ArmTeleop, TeleopConfig, FAULT
from pico_teleop.kinematics import ArmKinematics
from pico_teleop.poses import load_start_pose
from pico_teleop.validate import FOLLOWER_LIMITS_DEG
NAME = {((1,0,0),1): "roll top->RIGHT", ((1,0,0),-1): "roll top->LEFT", ((0,1,0),1): "pitch DOWN", ((0,1,0),-1): "pitch UP"}
for fwd in (0.0, 0.10, 0.20, 0.30):   # gripper forward (m); hand moves 2x
    row = []
    for (ax, sgn), nm in NAME.items():
        arm = ArmTeleop("left", ArmKinematics("left", limits_deg=FOLLOWER_LIMITS_DEG["left"], margin_deg=2.0),
                        load_start_pose("left"), TeleopConfig(engage_hold_s=0.0, orientation=True))
        arm.step(ADAPT(_c(grip=0.0), True)); arm.step(ADAPT(_c(), True))
        out = None; ok = True
        for i in range(1, 401):   # move forward slowly
            out = arm.step(ADAPT(_c(delta=(2 * fwd * min(i / 300, 1), 0, 0)), True))
            if out.state == FAULT: ok = False; break
        if not ok: row.append(f"{nm}: reach FAULT ({out.event[7:40]})"); continue
        base = (2 * fwd, 0, 0); maxok, why = 0, "40+"
        for i in range(1, 401):
            out = arm.step(ADAPT(_c(ax, sgn * i * 0.1, delta=base), True))
            if out.state == FAULT: why = out.event[7:22] + out.event[7:60].split(" ")[0][:0]; break
            maxok = i * 0.1
        row.append(f"{nm} {maxok:.0f}" + ("" if why == "40+" else f" ({why.strip()})"))
    print(f"gripper {fwd*100:2.0f} cm forward: " + " | ".join(row))
