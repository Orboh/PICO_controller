import json,sys,math
import numpy as np, pinocchio as pin
from pico_teleop.frames import controller_in_robot
t0=None; prevR=None; prevt=None; speeds=[]
for l in open(sys.argv[1]):
    d=json.loads(l)
    if 't' not in d: continue
    t0=t0 or d['t']; t=d['t']-t0; a=d['arms']['left']; e=a.get('event') or ''
    pose=d['frame']['left']['pose']
    if any(pose[3:]):
        _,R=controller_in_robot(pose)
        if prevR is not None and t>prevt:
            speeds.append((t, math.degrees(np.linalg.norm(pin.log3(R@prevR.T)))/(t-prevt)))
        prevR,prevt=R,t
    if e and ('WALL' in e or 'FAULT' in e) and 'refused' not in e:
        w=[s for tt,s in speeds if t-1.0<=tt<=t]
        print(f"{t:6.1f}s hand turn speed (last 1 s) max {max(w) if w else 0:5.0f} deg/s, median {np.median(w) if w else 0:4.0f} | {e[:62]}")
