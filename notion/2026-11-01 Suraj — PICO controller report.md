# 2026-11-01 Suraj — PICO controller: robot pilot, wrist and right arm, the wall fix, video pipeline

Work date: 2026-11-01 · Author: Suraj (written with Claude Code) · Repo `~/projects/PICO-controller` (local git, branch `v12-pilot-pico-redo-log`, not pushed) · Logs `log/log-025` … `log-031`

## Summary
- **PICO teleop on the real robot, both arms.** Left arm: data-collection block with the new joint_1 limit, and wrist roll/pitch. Right arm: first ever PICO session, position (all directions right) and roll left.
- **Main problem today: 4 sessions ended LOCKED** in the same way: the arm reached the edge of its workspace (a joint limit or a stretched arm) → FAULT → the operator squeezed again in the same pose → FAULT again → LOCKED → session over.
- **Fix written ("wall")**: at the edge the arm now stops and holds, the hand slips, moving back works at once; no FAULT, no lock. Offline: today's locked sessions run with **0 FAULTs and 0 locks**. **Not yet tried on the robot.**
- **Video (FPV) pipeline with an on/off button on the controller** is built but **not used** for now (Wi-Fi load next to the controller input).

## 1. Sessions today (all supervised; arm resting, power off at the end)
| Session | Arm | Operator / spotter | What | Result | Log |
| --- | --- | --- | --- | --- | --- |
| PICO pilot block (5 episodes) | left | Kota-san / Suraj | data collection with joint_1 limit 120° | ep 0 + ep 1 good (joint_1 swung ~105°: the 120° change works), then **LOCKED** (stretched arm) | log-025 |
| W1 wrist | left | Kota-san / Suraj | yaw, roll, pitch | yaw OK; roll/pitch hit joint_6 → **LOCKED** | log-027 |
| W1b wrist | left | Kota-san / Suraj | roll and pitch from 10 cm forward | roll ±38°, pitch ±15°, **0 FAULTs** | log-027 |
| R1 position | **right (first time)** | Kento-san / Suraj | forward, left, right, up, down | 13 squeezes, **0 FAULTs**, all directions right | log-029 |
| R2 wrist | right | Kento-san / Suraj | roll and pitch | roll left OK; roll right hit joint_2 → **LOCKED**; pitch not reached | log-029 |

Approvals today: Kota-san in person (joint_1 120° limit; right arm). To do: a one-line written OK in Slack.

## 2. What passed offline vs. what was confirmed on the robot
| Check | Offline (no robot) | On the robot |
| --- | --- | --- |
| joint_1 drift limit 120° | tests + replay | ✅ ep 0 swung ~105° without a FAULT |
| Wrist follows the hand 1:1 (roll, pitch, yaw) | tests, dry run | ✅ left (command = hand on every turn); right roll left |
| Right arm directions | dry run | ✅ R1 (spotter) |
| Pre-move: Enter moves the arm 10 cm forward slowly | tests (path never below start height) | ❌ not used yet |
| **Wall instead of FAULT at the edge** | ✅ 11 new/updated tests; replays below | ❌ **not tried yet** |
| Video on/off from the controller | tests | ❌ not used (decision) |

## 3. Problems and fixes
| Problem | Cause | Fix | Status |
| --- | --- | --- | --- |
| Pilot locked after 2 episodes | arm stretched far forward ("near singular") → FAULT, re-squeeze → LOCKED | wall | offline ✅ |
| Left roll/pitch locked | joint_6 has only ±45°; start pose +19° leaves ~21° | start 10 cm forward (all directions ≥ 27° of room); wall | robot ✅ (W1b) |
| Right roll locked | 24 cm forward reach in wrist mode pushed joint_2 to its limit edge | wall | offline ✅ |
| Arm did not move in one run (17:49) | follower side (power / start order) | order: power on, then the follower | robot ✅ |
| Commands broke when pasted | long commands wrapped | one-word scripts (`arm_session.sh`, `wrist_dry_run.sh`) | ✅ |

### The wall fix (log-030)
- Edge checks that used to FAULT now **hold the arm**: joint within 3° of its limit, arm stretched (0.02–0.03; below 0.02 still a FAULT), joint past its session drift limit, joint > 60° from where you squeezed, target out of reach. Only a move that goes **deeper** is blocked; moving out or sideways works. The hand "slips", so pushing further does nothing and moving back follows at once.
- Kept as FAULTs (something is wrong): lost/jumping tracking, hand lag, IK jump, wrist cannot reach a rotation, link loss. The lock after repeated FAULTs is unchanged.
- Wall time and hand slip are logged per cycle and printed at the end, so episodes recorded against a wall can be marked.
- Reviewed by Elephant and Goldfish before coding. Deferred: joint-limit steering in wrist mode (helps little on the wrist joints, can cause new FAULTs). Not possible: a wall signal inside the headset (controller vibration is not in the SDK).

Replays of real sessions, old code vs. new code (same recorded hand motion, no robot):
| Session | Old code | New code |
| --- | --- | --- |
| W1 left wrist (locked) | 2 FAULTs, **LOCKED** | **0 FAULTs**, 1.8 s at a wall |
| R2 right wrist (locked) | 2 FAULTs, **LOCKED** | **0 FAULTs**, 0.4 s at a wall |
| W1b left wrist, R1 right position | 0 FAULTs | 0 FAULTs (unchanged) |
| Session 4 (10-31, 58 FAULTs originally) | locked at 80 s | ran 389 s; locked only on real tracking jumps; no joint closer than 3.0° to a limit, drift within limits (no creep) |

## 4. Video in the headset (log-031)
- Built the Orboh video sender (`XRoboToolkit-Orin-Video-Sender`, branch `vector-x86`, from the Notion FPV page) on the operator PC; chest camera `/dev/video4`.
- **Hold the LEFT controller's Y for 1 s** = video on / off (A and B are used by the app).
- **Not used for now**: the video adds ~4 Mbit/s on the same Wi-Fi as the controller input. Not tested in the headset. Cannot run while lerobot-record uses the same camera (needs the camera splitter + sudo installs).

## 5. Next steps
1. [ ] Short review of the finished wall code (Elephant + Goldfish)
2. [ ] Robot wall check (one arm, separate spotter): push slowly into a joint limit, the stretched arm and the 60° posture edge → arm holds, `WALL` shown, moving back works, no FAULT / lock
3. [ ] Repeat R2 right wrist (roll right, pitch) using the pre-move (Enter)
4. [ ] PICO block × 5 (left) with the wall; wall time per episode from the log
5. [ ] Kento-san: joint_7 leader offset (~89°), then redo the leader block; then compare leader vs. PICO
6. [ ] Written approval (Slack) for today's in-person OKs

## 6. Where things are
| What | Location |
| --- | --- |
| Code | `~/projects/PICO-controller/pico_teleop/` (wall: `controller.py` `_edge`, `_hit_wall`; pre-move and video: `sender.py`, `video.py`) |
| Scripts | `scripts/arm_session.sh left|right position|wrist` (`PREMOVE_CM=10`, `VIDEO=/dev/video4`), `scripts/video_check.sh`, `scripts/wrist_dry_run.sh` |
| Logs | `log/log-025` (pilot), `-026` (what guides the robot), `-027` (left wrist), `-028` (pre-move), `-029` (right arm), `-030` (wall), `-031` (video) |
| Run records | `runs/pilot-pico-3-*`, `runs/wrist-left-20261101-*`, `runs/position-right-20261101-181616-*`, `runs/wrist-right-20261101-182038-*` |
| Datasets (local only) | `~/.cache/huggingface/lerobot/Orboh/openarm_pilot_pico_tmp_20261101_170706` |
| Plan | README "Plan in progress" |
