import sys; sys.path.insert(0, "tests")
import numpy as np
from test_orientation import _c, ADAPT
from pico_teleop.controller import ArmTeleop, TeleopConfig, FAULT
from pico_teleop.kinematics import ArmKinematics
from pico_teleop.poses import load_start_pose
from pico_teleop.validate import FOLLOWER_LIMITS_DEG
AX = {"roll": (1, 0, 0), "pitch": (0, 1, 0)}
NAME = {("roll", 1): "roll top->RIGHT", ("roll", -1): "roll top->LEFT", ("pitch", 1): "pitch front DOWN", ("pitch", -1): "pitch front UP"}
side = sys.argv[1]
for name, ax in AX.items():
    for sgn in (1, -1):
        arm = ArmTeleop(side, ArmKinematics(side, limits_deg=FOLLOWER_LIMITS_DEG[side], margin_deg=2.0),
                        load_start_pose(side), TeleopConfig(engage_hold_s=0.0, orientation=True))
        arm.step(ADAPT(_c(grip=0.0), True)); arm.step(ADAPT(_c(), True))
        maxok, why = 0, "ok to 40"
        for i in range(1, 401):  # 0.1 deg per step, up to 40 deg
            d = sgn * i * 0.1
            out = arm.step(ADAPT(_c(ax, d), True))
            if out.state == FAULT: why = out.event.split("(targets")[0][7:60]; break
            maxok = i * 0.1
        q = np.rad2deg(arm.q)
        print(f"{side:5} {NAME[(name, sgn)]:18}: max {maxok:4.0f} deg  stop: {why}  (j6 {q[5]:+.0f}, j7 {q[6]:+.0f})")
