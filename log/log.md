# Work log index — PICO-controller

Rules for this folder:
- One topic per file: `log-001.md`, `log-002.md`, …, in the order the work happened.
- Each log says **which option we chose, why, and what each function does and where** (`file:line`).
- No file may be longer than **180 lines**, this index included. A topic that grows past 180 lines continues in the next numbered file.
- Add one row here for every new log.

| File | Why this log exists |
|------|---------------------|
| [log-001.md](log-001.md) | Created the `PICO-controller` project folder and this log folder: the folder name and the log rules. |
| [log-002.md](log-002.md) | Read-only inspection: commits, the receiver/follower contract, the SDK's stale-value behaviour, NaN-to-upper-limit and no-follower-feedback findings, and why the code lives here. |
| [log-003.md](log-003.md) | Gate 1: guided input-only diagnostic (`xr_input.py`, `diag_input.py`), 12 offline tests, Elephant/Goldfish findings and fixes; live PASS pending the headset. |
| [log-004.md](log-004.md) | Gate 2 offline: frames, per-side IK, packet validator, HOLD/ENGAGED/FAULT clutch, gripper soft takeover; 43 tests, trajectory plots; bugs found in the plots (false jump, IK staircase) fixed; right-arm posture-swing finding. |
| [log-005.md](log-005.md) | Milestone-2 Elephant/Goldfish findings and fixes: real fingertip TCP (URDF tcp is the wrist), pose set in force, valid-only releases, analog checks, posture fault, device-independent core, README controls. |
| [log-006.md](log-006.md) | Gate 3: sender vs the real lerobot receiver on localhost (56 tests), NaN pass-through confirmed, record 'a' key; the follower-pose anchor gap and the proposed receiver patch; gate 4 plan. |
| [log-007.md](log-007.md) | Gate 1 LIVE PASS (5 phases, evidence/gate1): how to connect (orboh Wi-Fi, A toggles sending, 3D demo steals input), measured rate/gaps, grip is binary, put-down = frozen, hidden controller NOT detectable, headset sleeps after ~8 s; milestone-3 Elephant fixes in the sender; 62 tests. |
| [log-008.md](log-008.md) | Axis signs PASS without an arm (both controllers); a wrong mirror fix made and reverted (proof: controller geometry); log-007 corrections; milestone-3/4 review tables; grip held 0.3 s to engage; hand-jump and lag FAULTs; LINK DOWN keeps sending HOLD; milestone-5 review fixes (stop after 5 s link pause, step-size FAULT, foreign lock); 71 tests. |
| [log-009.md](log-009.md) | Online gate 3 PASS: live PICO → sender → real port 5701 → real lerobot receiver in remote_probe (no robot): first packet = start pose, live engage/release, hold on stop; gripper takeover finding; RTT measurement limit. |
| [log-010.md](log-010.md) | Gate 5: go/no-go signed; two supervised left-arm sessions (operators Suraj and Kota-san); directions confirmed on the real arm; all FAULTs behaved as designed; tuning findings (Wi-Fi, 45° posture limit, gripper takeover). |
| [log-011.md](log-011.md) | Reviews of the arm sessions: deviations listed (not clean checks), periodic 15 s stalls + PC Wi-Fi power save, runbook rewrite (daily go/no-go, R1-R4, esc after release), posture limit 60°, gripper options. |
| [log-012.md](log-012.md) | Gripper gentle sync (30°/s until the trigger matches, then 120°/s) replaces the soft takeover; options compared; 72 tests; −60° still to measure on the arm. |
| [log-013.md](log-013.md) | Periodic 15 s input stalls: PC Wi-Fi power save turned off (not the cause), link pings fine → headset/app side; stale limit 0.25 → 0.4 s (`--stale-s`), replay of session 2: 2 → 0 stale FAULTs; 72 tests. |
| [log-014.md](log-014.md) | Arm session 3 (Kota-san / Suraj): directions and gripper by trigger work on the real arm, 0 stale FAULTs with 0.4 s, correct esc order; next: wrist rotation (6-DOF IK) and gripper force feedback (needs follower feedback; PICO haptics not in the SDK binding). |
| [log-015.md](log-015.md) | Wrist rotation: relative 6-DOF IK behind `--orientation` (off by default), 90°/s limit, rotation-jump and unreachable FAULTs; 11 new tests (83 total); replay of session 3 follows the real wrist; next: headset dry run, then arm. |
| [log-016.md](log-016.md) | Wrist dry run: swapped-looking axes were the operator facing −100° (headset data), math matches upstream XRoboToolkit; new facing guard (refuse/FAULT beyond 30°); 90 tests. |
| [log-017.md](log-017.md) | Arm session 4 (wrist, Suraj/Kota-san): 100 engages / 58 FAULTs, re-engage loops crept the arm to joint limits; fixes: same-FAULT lockout, 60° session drift limit, softer orientation; replay 14 engages / 5 FAULTs, locked at 135 s; 93 tests. |
| [log-018.md](log-018.md) | Review of the session-4 fixes: number-free FAULT kinds, lock on 3 mixed FAULTs/60 s, one refusal per squeeze, 3° joint-limit guard, rotation gap vs the hand (0.5 s), fallback exercised; replay locks at 80 s; 97 tests. |
| [log-019.md](log-019.md) | Arm session 5 (wrist rotation, fixed controller): forward/roll/pitch/yaw followed 1:1, 0 FAULTs; deviation: operator and spotter were the same person (rule needs a second person at the arm). |
| [log-020.md](log-020.md) | Gate 4 PASS: lerobot-record with the PICO (chest + left wrist, 30 fps), follower tracks the actions (median 0.32°); receiver pauses 1-6 s measured; the head-angle facing guard blocked engaging (operator looks at the arm, +45-58°): proposal to check only at engage, 60-75°. |
| [log-021.md](log-021.md) | Pilot block 1 (leader × 5, Kota-san): attempt 1 recorded 0 (keyboard focus), attempt 2: 5 episodes; finding: leader-mode episodes start with follower joint_7 ≈ +25° vs the pose set −63° (~89° apart) → calibration question for Kento. |
| [log-022.md](log-022.md) | Pilot block 2 (PICO) locked after ep 0 (joint_2 near its limit); null-space limit avoidance ON (8° zone) and drift limit 90°; replay 3 → 10 engages; 100 tests. |
| [log-023.md](log-023.md) | Pilot block 2 redo (PICO): ep 0 good, then LOCKED on the joint_1 90° drift limit; leader demos need ≈100-107° on joint_1 → options (j1 120° recommended), not applied. |
| [log-024.md](log-024.md) | Option A: joint_1 drift limit 120°, others 90° (`_worst_drift`); review fixes: numbers vs session start (leader 113°, 7° margin), session-4 replay unchanged (locks 80 s), step-path test, runbook pilot procedure + stop rules; 102 tests; robot check pending. |
| [log-025.md](log-025.md) | Robot pilot PICO block with joint_1 120°: ep 0 swung j1 ≈105° (option A works), ep 1 good, then "near singular" FAULT twice → LOCKED (arm stretched further than any leader demo); options wall / FAULT-only-when-worsening / lower threshold. |
| [log-026.md](log-026.md) | What guides the robot: controller pose → IK, the robot elbow is chosen by the program (not the human elbow); widely used methods with sources; review fixes (20° wrist turns FAULT offline → 10° only; `arm_session.sh`; right-arm runbook); plan 0 / W1 / R1 / R2. |
| [log-027.md](log-027.md) | W1 left wrist: yaw OK; roll/pitch hit joint_6 at the start pose → LOCKED; room per direction (10 cm forward is best); W1b from forward: roll ±38°, pitch ±15° commanded 1:1, 0 FAULTs. |
| [log-028.md](log-028.md) | Pre-move for wrist tests: Enter in terminal A moves the arm 10 cm forward at 10°/s with the same gripper rotation (`premove_forward`, `PREMOVE_CM`); path never below the start height; 104 tests; not yet on the arm. |
