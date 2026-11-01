## 9. Update (late): repeat wall check on the robot — PASS (log-034)
- Left arm, wrist mode, with the rotation-wall version. **417 s, 63 squeezes, 94 wall contacts, 1 FAULT, no lock.**
- Walls by kind: joint_2 at its limit 43×, joint_1 120° from the start 19×, joint_6 at its limit 16×, joint_7 90° from the start 8×, wrist turned faster than the gripper 7×, joint_7 at its limit 1×. **None caused a FAULT.**
- The only FAULT was a real controller tracking jump (17 cm in one update), cleared by releasing the grip.
- The limits held exactly at the wall values (≥ 3° from every joint limit; joint_1 ≤ 120°, others ≤ 90° from the start).
- Finding: joint_2 is the most common wall on the left arm in wrist mode (start pose close to its limit; joint-limit steering is off in wrist mode) → Kento's start pose with more room, or limit steering in wrist mode (to check offline).

| Check | Offline | On the robot |
| --- | --- | --- |
| Position walls | ✅ | ✅ |
| Rotation edge as a wall | ✅ | ✅ 11-01 late (no lock) |
| 60 Hz + ease in/out (jerk) | ✅ | ❌ next |
