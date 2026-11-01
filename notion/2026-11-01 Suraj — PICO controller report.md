# 2026-11-01 Suraj — PICO controller: robot pilot, wrist and right arm, the wall fix, video pipeline

Work date: 2026-11-01 · Author: Suraj (written with Claude Code) · Repo `~/projects/PICO-controller` (local git, branch `v13-collection-gui`, latest `1508dd1`, not pushed) · Logs `log/log-025` … `log-043`

## Summary
- **PICO teleop on the real robot, both arms.** Left arm: data-collection block with the new joint_1 limit, wrist roll/pitch/yaw. Right arm: first ever PICO session (position: all directions right; roll left).
- **Main problem: 4 sessions ended LOCKED** the same way: the arm reached the edge of its workspace → FAULT → the operator squeezed again in the same pose → FAULT again → LOCKED → session over.
- **Fix: the "wall".** At the edge the arm now stops and holds, the hand slips, moving back works at once; no FAULT, no lock. **Verified on the robot (left arm): 94 wall contacts in 7 minutes, no lock.**
- **PICO episodes were jerkier than leader episodes**, mainly because the sender (50 Hz) and the recorder (30 fps) run at different rates. Fix (60 Hz + ease in/out) **confirmed on the robot: jerk −45 %, now as smooth as the leader episodes.**
- **Video (FPV) pipeline with an on/off button on the controller**: built, **not used** for now (Wi-Fi load).
- **Collection website (new)**: one page for both methods (leader arm or PICO) instead of two terminals: connection checks with notifications, the dry → gate → session stages with the reviewer's OK (as in the Dream-Lab Collect tab), the runbook's order and refusals, live status, episode notes, English / 日本語. Built and reviewed (Elephant + Goldfish); **not yet used on the robot**. Guides: "Working setup for PICO" and "Collection website — setup and operation" (new pages).
- **Direction**: the PICO now moves the **follower** arm (tested); a new mode where the PICO moves the **leader** arm is planned.
- Reviewed by Elephant + Goldfish at the end of the day; 4 code fixes applied (§3).

## 1. Sessions today (all supervised; arm resting, power off at the end)
| Session | Arm | Operator / spotter | What | Result | Log |
| --- | --- | --- | --- | --- | --- |
| PICO pilot block (5 episodes) | left | Kota-san / Suraj | data collection with joint_1 limit 120° | ep 0 + ep 1 good (joint_1 swung ~105°: the 120° change works), then **LOCKED** (stretched arm) | log-025 |
| W1 wrist | left | Kota-san / Suraj | yaw, roll, pitch | yaw OK; roll/pitch hit joint_6 → **LOCKED** | log-027 |
| W1b wrist | left | Kota-san / Suraj | roll and pitch from 10 cm forward | roll ±38°, pitch ±15°, **0 FAULTs** | log-027 |
| R1 position | **right (first time)** | Kento-san / Suraj | forward, left, right, up, down | 13 squeezes, **0 FAULTs**, all directions right | log-029 |
| R2 wrist | right | Kento-san / Suraj | roll and pitch | roll left OK; roll right hit joint_2 → **LOCKED**; pitch not reached | log-029 |
| Wall check 1 | left | — / Suraj | push into the edges | 5 walls, none FAULTed; locked from the wrist-turning side → fixed | log-033 |
| Wall check 2 | left | — / Suraj | push into the edges for 7 min | **PASS: 63 squeezes, 94 walls, 1 real tracking FAULT, no lock** | log-034 |
| Smoothing test | left | — / Suraj | PICO episodes with 60 Hz + ease in/out | jerk 24.9 k (−45 %), as smooth as the leader; then **LOCKED** on link losses while the recorder saved → fixed | log-037 |
| Wall check right | right | Kento-san / Suraj | joint limit, reach forward, re-squeeze, fast wrist turn | **PASS: 25 squeezes, 55 walls, no lock** (only FAULT: link lost because `esc` was pressed while still squeezing) | log-036 |

Approvals today: Kota-san in person (joint_1 120° limit; right arm; the wall behaviour). **To do: a written OK in Slack.**

## 2. What passed offline vs. what was confirmed on the robot
| Check | Offline (no robot) | On the robot |
| --- | --- | --- |
| joint_1 drift limit 120° | tests + replay | ✅ ep 0 swung ~105° without a FAULT |
| Wrist follows the hand 1:1 (roll, pitch, yaw) | tests, dry run | ✅ left (command = hand on every turn); right roll left |
| Right arm directions | dry run | ✅ R1 (spotter) |
| Walls: joint limit, drift, wrist turning | ✅ | ✅ left and right arm (no lock) |
| Walls: posture (60° per squeeze), wrist cannot keep the hand's rotation | ✅ | ✅ right arm |
| Walls: joint jump | ✅ | ✅ smoothing test |
| Walls: stretched arm, out of reach | ✅ | ❌ not seen on the robot: reaching forward, the joint_1 120° wall stops the arm first |
| Pre-move: Enter moves the arm 10 cm forward slowly | tests | ❌ not used yet |
| 60 Hz + ease in/out (jerk) | ✅ −44…66 % jerk | ✅ −45 %, step wobble at leader level (log-037) |
| Video on/off from the controller | tests | ❌ not used (decision) |
| Collection website (both methods, checks, stages, EN/JA, both arms with the leader arms) | ✅ 146 tests, browser check, live connection checks green | 🟡 headset-only check: PICO link OK (~53 Hz); one run with a **powered** follower (20:50) moved it to the start pose but recorded **no episode** (keys bug, log-042); no saved episode from the page yet |

The limit numbers measured on the robot are the values **sent** to the arm (no feedback from the arm yet); that the arm held at each wall was judged by the spotter.

## 3. Problems and fixes
| Problem | Cause | Fix | Status |
| --- | --- | --- | --- |
| Pilot locked after 2 episodes | arm stretched far forward → FAULT, re-squeeze → LOCKED | wall | offline ✅ (stretched-arm wall not yet seen on the robot) |
| Left roll/pitch locked | joint_6 has only ±45°; start pose +19° leaves ~21° | start 10 cm forward (every direction ≥ 27° of room); wall | robot ✅ |
| Right roll locked | 24 cm forward reach in wrist mode pushed joint_2 to its limit edge | wall | offline ✅ |
| Wall check 1 locked | fast wrist turns at the limit: "hand turned ahead of the gripper" was still a FAULT | the wrist-turning side is a wall too | robot ✅ (wall check 2) |
| PICO episodes jerkier than leader | sender 50 Hz vs recorder 30 fps; hard speed cut; input gaps | 60 Hz + ease in/out (opt-in) | robot ✅ (−45 %) |
| Smoothing test locked | the operator squeezed while the recorder saved an episode → "link lost" twice | a link pause no longer counts toward the lock (still freezes the arm) | offline ✅ |
| Roll/pitch/yaw did not work in the recording runs | the recording script runs position mode (by design, fair comparison) | `WRIST_MODE=1` for recording with wrist rotation | ✅ |
| Arm did not move in one run (17:49) | follower side (power / start order) | order: power on, then the follower | robot ✅ |
| Commands broke when pasted | long commands wrapped | one-word scripts (`arm_session.sh`, `wrist_dry_run.sh`) | ✅ |

### The wall (log-030, log-033)
- These edges no longer FAULT; the arm **holds**: joint within 3° of its limit, arm stretched, joint past its session drift limit (120° joint_1 / 90°), joint > 60° from where you squeezed, target out of reach, a big joint jump (never sent), wrist turned faster than the gripper (extra turn dropped), a move that would tilt the gripper away from the hand. Only a move that goes **deeper** is held; moving out or sideways works; the hand "slips".
- Still FAULTs (something is wrong): lost or jumping tracking, hand far ahead of the arm, link loss, a move deeper below the hard stretched-arm floor. The lock after repeated FAULTs is unchanged.
- The operator cannot see the wall message in the headset (no controller vibration in the SDK) → **the spotter calls "wall, move back"** (runbook).
- Wall time and hand slip are logged per cycle (`wall` in the sender log) and printed at the end, to mark episodes recorded against a wall.
- Deferred: joint-limit steering in wrist mode. Finding from wall check 2: **joint_2 is the most common wall on the left arm** (43 of 94): the left start pose leaves it little room → Kento's start pose, or steering in wrist mode.

Replays of real sessions, old code vs. final code (same recorded hand motion, no robot):
| Session | Real result | Final code |
| --- | --- | --- |
| Pilot block 11-01 (928 s) | LOCKED | 0 FAULTs, no lock |
| Pilot redo 10-31 | LOCKED | 0 FAULTs, no lock |
| Left wrist W1 / right wrist R2 | LOCKED | 0 FAULTs, no lock |
| Wall check 1 | LOCKED | 1 real tracking FAULT, no lock |
| Wall check 2 | no lock | identical to the robot (63 squeezes, 1 FAULT, no lock): the offline bench matches the robot |
| Session 4 (10-31, 58 FAULTs) | many FAULTs | 2 real tracking FAULTs, no lock, no creep (≥ 3° from every limit) |

### Jerk: why PICO episodes are less smooth (log-032)
- Not mainly the speed limit. The PICO sender sends 50 targets per second, the recorder takes 30 per second → small/big/small steps (61–63 % of frames vs 16–17 % for the leader), a ~10 Hz wobble. Smaller causes: the hard cut at the speed limit, short PICO input gaps.
- Fix (opt-in, defaults unchanged): sender at **60 Hz** + **ease in/out** (acceleration limit 300°/s²).

| Setting (offline bench, real PICO input, recorder at 30 fps) | Jerk ep 0 | Jerk ep 1 | Path change |
| --- | --- | --- | --- |
| today (50 Hz) | 40 k | 66 k | — |
| **60 Hz + 300°/s²** | **22.5 k (−44 %)** | **22 k (−66 %)** | ~1° (95 %) |
| leader episodes | 27–39 k | | |
- With ease in/out a joint can end up to 0.5° inside the 3° guard (never closer than 2.5°).
- **Robot (log-037):** PICO 60 Hz + 300°/s² episode: jerk 24.9 k (−45 %), step doubling 17 % (50 Hz: 61–63 %, leader: 12–17 %).

### Review at the end of the day (Elephant + Goldfish, log-035)
| Problem found | Fix |
| --- | --- |
| An arm already very stretched could not move out (every move FAULTed → lock) | only a move that makes it straighter FAULTs |
| The per-cycle "wall" mark in the log was too wide (12 072 cycles vs 4 620 real) | marks only cycles where the arm was held; cleared on release / FAULT |
| A squeeze or lost connection did not cancel the pre-move | both cancel it; pre-move only for one arm |
| Stopping the video could pause the control loop up to 6 s | never toggled while following; stops without waiting |
- Process gap: code went to the robot within minutes of each commit, before the review. New rule (runbook): offline replays + tests (+ wrist check for rotation changes) **before** every robot session.

## 4. Video in the headset (log-031)
- Built the Orboh video sender (`XRoboToolkit-Orin-Video-Sender`, branch `vector-x86`, from the Notion FPV page) on the operator PC; chest camera `/dev/video4`.
- **Hold the LEFT controller's Y for 1 s** = video on / off (A and B are used by the app); never switched while the arm is following.
- **Not used for now**: the video adds ~4 Mbit/s on the same Wi-Fi as the controller input. Not tested in the headset. Cannot run while lerobot-record uses the same camera (needs the camera splitter + sudo installs).

## 5. Next steps
**Done today**
- [x] Review of the wall code (Elephant + Goldfish) — fixes applied (§3, log-035)
- [x] Robot wall check on the left arm — PASS (wall check 2, log-034)

**Permissions to get (written, Slack) — owner: Suraj → Kota-san**
- [ ] joint_1 session drift limit 120° (in person 11-01)
- [ ] Using the right arm with the PICO (in person 11-01)
- [ ] The wall behaviour (arm holds at the edge instead of FAULT) on the robot (in person 11-01)

**Requests to Kento-san — owner: Suraj**
- [ ] joint_7 leader/follower start offset (~89°): needed for a fair leader vs. PICO comparison
- [ ] Left start pose with more room for joint_2 (most common wall: 43 of 94)
- [ ] Confirm the follower branch `feat/takeei-wood-l1` as the one to use
- [ ] Proposal: a command channel in the lerobot record loop instead of typed keys (Slack draft in §3, website part)
- [ ] A two-arm remote receiver in lerobot (needed for both arms with the PICO)

**Decision to make — owner: Suraj**
- [ ] Episode rule for demonstrations recorded against a wall: mark an episode when it spends more than **N s at a wall** or has more than **M cm of hand slip** (numbers to decide); the check then reads the sender log, matched to the dataset frames by time

**Robot tests (supervised, separate spotter)**
- [x] Wall check on the **right** arm — PASS (55 walls, no lock, log-036); the stretched-arm wall was not reached (the joint_1 120° wall comes first)
- [x] 60 Hz + ease in/out — robot: jerk −45 %, as smooth as the leader (log-037); use `HZ=60 ACC=300` for PICO recording
- [ ] Repeat R2 right wrist (roll right, pitch) using the pre-move (Enter)

**Website (before the robot)**
- [x] Headset-only check of the website (arm off) — PICO link OK; recorder hang found and fixed (log-041)
- [ ] Supervised session with the website: dry with both methods (check "keys from this page ✅" and "saved 2 of 2"), then gate + reviewer OK
- [ ] Both arms with the leader arms from the page: dry

**Then data collection**
- [ ] PICO block × 5 (left) with the wall and `HZ=60 ACC=300` (add `WRIST_MODE=1` if the wrist should turn)
- [ ] After Kento-san's joint_7 fix: redo the leader block, then compare leader vs. PICO

### Collection website (log-038 … log-040)
| Part | What it does |
| --- | --- |
| 0 Before you start | PICO: PC on Wi-Fi `orboh`, PC Service, 3D demo closed, headset answers, PICO input test; leader arm CAN; follower CAN; cameras; today's go/no-go; no other sender. Red items say how to fix; a toast when a connection is lost or back |
| 1 Setup | PICO → follower / leader arm → follower, arm, **stage** (dry 2 / gate 10 / session), episodes, operator name, task; PICO: smooth motion, wrist rotation, pre-move |
| 2 Run | ① teleop → ② spotter name + "arm may move" → ③ recording → ▶ / ↺ / ⟲ / ■ End (not an e-stop) → ④; one recording per ①; refuses unsafe orders (R2, R4, LOCKED, wrong start pose, no reviewed gate) |
| Live status, episodes, logs | sender state, boundary time, input rate, facing; grasp / placed / note per episode, saved at once |
- Reviewed by Elephant (16 findings) and Goldfish (10); 3 blockers fixed (one recording per teleop start, crashed-sender status, go/no-go enforced).
- Start: `bash ~/projects/PICO-controller/scripts/gui.sh` → http://localhost:8090.
- **Headset-only check (log-041):** ① from the page worked (PICO ~53 Hz, correct start pose, HOLD, notes saved). ③ was pressed with the follower CAN link down: the recorder failed at once ("Network is down", the arm never moved) and then hung; it had to be stopped with Ctrl-C. Fixed: ② refuses the same person as operator and spotter; ③ refuses while the follower (or leader) CAN link is down; a red "RECORDER FAILED" banner and an "Abort recorder (Ctrl-C)" button (only when the recorder fails or does not end).
- **Correction (log-042/043):** the 20:50 run had the left follower **powered and connected** (it moved to the start pose, 28 `a` presses), not "arm off"; it ended without a normal end.
- **Recorder keys bug (log-042):** the page's ▶ / ■ did nothing because, with a desktop `DISPLAY`, lerobot reads → ← Esc from the PC's keyboard (any window), not from its terminal. Fixed with the existing key method: the recorder is started without `DISPLAY` (page and our scripts), the same as `collect_bimanual.sh` already does. Test with lerobot's real key reader.
- **Both arms (log-043):** with the leader arms the page runs the team's `collect_bimanual.sh` (dry / gate / session, like the Collect tab); **both arms with the PICO is not possible yet** (lerobot's remote receiver is one-arm only → Kento).
- **Review of log-040…042 (Elephant + Goldfish, log-043):** fixed — a short or failed gate can no longer be approved (review only with all episodes saved; a new gate cancels the OK); Abort only 60 s after End; check results expire and the Wi-Fi name is checked; scene required for gate/session; Japanese banners; the page shows "saved N of M episodes" and whether the recorder reads its keys from the page; guides updated (restart rule, power/CAN timing, recovery, data paths).

**Slack message to Kento-san (draft, to post in Slack):**

> Kento-san, a proposal for the recorder (lerobot fork), from Suraj.
> All our collection programs control `lerobot-record` only by keys (→ next/end, ← discard, a return, Esc stop): `record_pick_place_openarm.sh`, `record_bimanual_openarm.sh`, `collect_bimanual.sh`, the viser Collect tab (types keys into a pty) and our new PICO/leader collection website (same). With a desktop `DISPLAY` the keys come from the global keyboard (pynput); `collect_bimanual.sh` avoids it with `env -u DISPLAY` (line 141), our single-arm path did not (fixed today, log-042).
> **Proposal:** a small command channel in the record loop (e.g. a local socket or a FIFO next to the run folder) that sets the same `events` flags as the keys (`exit_early`, `rerecord_episode`, `align_start_pose`, `stop_recording`) and reports the state back (waiting / recording episode N / saving). Keys stay as they are; pages and scripts could then call real functions instead of typing keys, and know the recorder's state without parsing the console.
> Key files: lerobot `src/lerobot/common/control_utils.py` (`init_keyboard_listener`, `_init_stdin_key_listener`), `src/lerobot/scripts/lerobot_record.py` (record loop, waiting / save / start-pose gate); openarm-orboh `scripts/collect_bimanual.sh` (line 141), `scripts/record_pick_place_openarm.sh`; Dream-Lab-OpenArm `october 23/workspace/collect.py` (`KEYS`, pty); PICO-controller `pico_teleop/gui/session.py` (`KEYS`, `PtyChild`, `recorder_command`), log-042.
> No change needed now; would you consider it for the fork? (日本語: 収集の各プログラムはキー入力でしか lerobot-record を操作できないので、record ループに小さなコマンド経路を足す提案です。キー操作はそのまま残ります。)

## 6. Where things are
| What | Location |
| --- | --- |
| Code | `~/projects/PICO-controller/pico_teleop/` (wall: `controller.py` `_edge`, `_hit_wall`, `_note_wall`; pre-move and video: `sender.py`, `video.py`) |
| Scripts | `scripts/arm_session.sh left/right position/wrist` (`PREMOVE_CM=10`, `VIDEO=/dev/video4`), `scripts/gate4_sender.sh` (`HZ`, `ACC`), `scripts/video_check.sh`, `scripts/wrist_dry_run.sh` |
| Offline tools | `tools/` (fastreplay, jerkbench, wristcheck, rotspeed, wristsim, frames.sh) |
| Logs | `log/log-025` (pilot) … `log-035` (review), `log-036` (right-arm wall check), `log-037` (smoothing test), `log-038`…`log-043` (website); index `log/log.md` |
| Run records | `runs/pilot-pico-3-*`, `runs/wrist-left-20261101-*`, `runs/position-right-20261101-181616-*`, `runs/wrist-right-20261101-182038-*` |
| Datasets (local only) | `~/.cache/huggingface/lerobot/Orboh/openarm_pilot_pico_tmp_20261101_170706` |
| Safety runbook | `docs/hardware-runbook.md` (spotter card incl. walls and the pre-move) |
| Plan | README "Plan in progress" |
| Website | `pico_teleop/gui/` (`session.py`, `app.py`, `checks.py`, `i18n.py`), `scripts/gui.sh`, guides `docs/collection-website.md`, `docs/pico-working-setup.md` |
