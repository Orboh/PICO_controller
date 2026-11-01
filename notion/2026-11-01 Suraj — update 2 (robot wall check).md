## 8. Update (night): wall check on the robot (left arm, log-033)
- **Walls work on the robot:** 5 wall contacts (3× joint_1 120° from the session start while reaching forward, 2× joint_6 at its limit while turning the wrist). **None of them caused a FAULT**; the arm stopped and held.
- **The session still locked**, from the rotation side: right after the joint_6 wall the operator kept turning the wrist fast (100–500°/s, the gripper may turn 90°/s) → "hand turned 43° ahead of the gripper" FAULT twice → LOCKED. One more FAULT was a real tracking jump of the controller (kept as a FAULT).
- **Fixed offline:** the rotation edge is now a wall too. A turn faster than the gripper slips (the extra turn is dropped); a move that would make the gripper tilt away from the hand is held. Tests: 116 passed.
- Replay of this robot run with the fix: **no lock** (only the real tracking-jump FAULT); all earlier locked sessions: 0 locks.

| Check | Offline | On the robot |
| --- | --- | --- |
| Position walls (joint limit, drift, stretched arm) | ✅ | ✅ 11-01 night |
| Rotation edge as a wall | ✅ | ❌ repeat the wall check with this version |
| 60 Hz + ease in/out (jerk) | ✅ | ❌ |

Next: repeat the wall check with the new version (incl. a fast wrist turn at the limit), then a PICO block × 5 with `HZ=60 ACC=300`.
