"""Per squeeze: hand turn vs commanded gripper turn (robot frame, deg) at the moment of the largest hand turn."""
import json, sys
import numpy as np, pinocchio as pin
from pico_teleop.frames import controller_in_robot
from pico_teleop.kinematics import ArmKinematics, deg_dict_to_q
from pico_teleop.validate import FOLLOWER_LIMITS_DEG
side = sys.argv[2]
k = ArmKinematics(side, limits_deg=FOLLOWER_LIMITS_DEG[side], margin_deg=2.0)
t0 = None; sq = None; n = 0
def finish(sq):
    if not sq or not sq["rows"]: return
    r = max(sq["rows"], key=lambda x: np.linalg.norm(x[1]))
    print(f"squeeze {sq['n']} at {sq['t']:5.1f}s ({sq['state_end']}): hand rot x{r[1][0]:+5.0f} y{r[1][1]:+5.0f} z{r[1][2]:+5.0f} | "
          f"gripper cmd x{r[2][0]:+5.0f} y{r[2][1]:+5.0f} z{r[2][2]:+5.0f} | hand moved {r[3]*100:4.1f} cm | "
          f"j5/j6/j7 change {np.round(r[4],0).astype(int).tolist()} | j6 now {r[5]:+.0f}")
for l in open(sys.argv[1]):
    d = json.loads(l)
    if "t" not in d: continue
    t0 = t0 or d["t"]; a = d["arms"][side]; e = a.get("event") or ""
    pose = d["frame"][side]["pose"]
    if not any(pose[3:]): continue
    p, R = controller_in_robot(pose)
    if a["action"] is None: continue
    q = deg_dict_to_q(a["action"]); M = k.fk(q)
    if "ENGAGED: anchored" in e:
        finish(sq); n += 1
        sq = dict(n=n, t=d["t"] - t0, R0=R, p0=p, G0=M.rotation, q0=np.array([a["action"][f"joint_{i}.pos"] for i in (5, 6, 7)]), rows=[], state_end="")
        continue
    if sq is None: continue
    if a["state"] == "ENGAGED":
        hv = np.degrees(pin.log3(R @ sq["R0"].T)); gv = np.degrees(pin.log3(M.rotation @ sq["G0"].T))
        jw = np.array([a["action"][f"joint_{i}.pos"] for i in (5, 6, 7)])
        sq["rows"].append((None, hv, gv, np.linalg.norm(p - sq["p0"]), jw - sq["q0"], jw[1]))
    elif not sq["state_end"]:
        sq["state_end"] = a["state"] + (" " + e[:40] if e else "")
finish(sq)
