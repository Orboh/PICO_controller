# Gate 5 — supervised real-arm sessions with the PICO (runbook + spotter card)

Status (2026-11-01): supervised sessions, approvals in person by Kota-san (written Slack OK still to get).
**Covered**: left arm position, gripper by trigger, wrist rotation (log-019/027), recording (log-020), joint_1 drift
120° (log-025), the WALL behaviour on the left arm (log-034); right arm position (log-029). Follower checkout
`openarm-orboh` `feat/takeei-wood-l1` @ e98e987, lerobot `feat/bi-openarm-soft-move-to` @ a720ec19.
**Not covered**: walls on the right arm; stretched-arm / out-of-reach / joint-jump walls seen on the robot; the
pre-move (Enter); `HZ=60 ACC=300`; the PICO video; both arms; a measured TCP; the default branches.
**Rule from the 11-01 review (log-035)**: after a code change, run the offline replays (`tools/fastreplay.py`) and the
test suite, and re-check go/no-go item 10 for rotation changes, **before** the next robot session.
Source rules: `notion/OpenArm 安全プロトコル…md` (archived copy), Kento's request §8-9.
If anything surprises anyone: **stop** (spotter card below).

## Go / no-go — fill a NEW dated column every session day (all PASS, else no session)
| # | Item | Pass condition | Kind | Owner | 2026-10-31 | 2026-11-01 |
|---|---|---|---|---|---|---|
| 1 | Gate 1 live | `GATE 1 … PASS` on record (log-007) | one-time per headset/app version | Suraj | PASS | PASS (log-007; input today 60 Hz, L ok, R ok) |
| 2 | Axis signs, no arm | dry-run table in README; plus the 2 cm one-axis check on the arm (step 6) | one-time per code change in `frames.py` | Suraj | PASS (left "left" row to redo cleanly) | PASS (no change in `frames.py`) |
| 3 | Emergency stop | located, pointed at, **pressed once with the arm off**, within the spotter's reach | **daily** | spotter (name) | done, Kento-san + Suraj (not "pressed once" on record) | PASS (spotter Suraj) |
| 4 | PSU switches | which switch = which arm, labelled, reachable by the spotter | **daily** | spotter (name) | done | PASS (spotter Suraj) |
| 5 | Arm and side | left arm only until the right arm's zero/sign is checked | per plan | Suraj + Kento | left | left; right approved by Kota-san 2026-11-01 for R1 (position 2 cm checks), then R2 (wrist) |
| 6 | Start pose and checkout | sender prints the start-pose sha = `sha256sum` of the follower's file; follower checkout branch + commit as above (or a newer one Kento names) | **daily** | Suraj | PASS (sha 2ec31fb82bf0) | PASS left sha 2ec31fb82bf0; **right sha a3b97b8e9d66** (start j1 −59.6, j4 87.2, j7 66.7); openarm-orboh `feat/takeei-wood-l1` @ e98e987 |
| 7 | Feedback | `proposals/lerobot-follower-feedback.patch` applied **or** Kota/Kento OK for R1-R4 **in writing** (link here) | until the patch lands | Kota / Kento | session 2 (the counted test): Kota-san operated and gave the OK; written in Slack 12:59 https://orbohworkspace.slack.com/archives/C0C5F68JX8R/p1793419165855809 (general wording; ask him to confirm the scope). Session 1 had no approval and does not count | PASS: Kota-san OK in person on 2026-11-01 for the 120° joint_1 limit (relayed by Suraj; no written record yet) |
| 8 | Standing mark | tape outside the arm's reach for the wearer; workspace clear | **daily** | spotter (name) | done | PASS (spotter Suraj; area left/front clear for a 120° shoulder swing) |
| 9 | Grasp point | `--tcp-offset-m` measured or 0.1025 accepted | one-time | Suraj | 0.1025 accepted | 0.1025 accepted |
| 10 | Wrist-rotation dry run | README "Wrist-rotation dry run" table PASS for both arms (log it) | before the first `--orientation` arm session; again after code changes | Suraj | PASS 2026-10-31 (log-016) | PASS: dry run 10-31 both arms (log-016); the turn math (`frames.py`, `controller.py:350`) is unchanged since; the later rotation changes (72f3c6b, 356bff0) were used on the real arm in session 5 (log-019, followed 1:1); 50360c9 and d809f38 touch only position-only mode and the drift limit |

## Roles
- **Spotter**: beside the arm, hand on the emergency stop, status terminal turned toward them.
  **The arm has priority over the terminal.** Only the spotter says "ready" and "esc".
- **Wearer/operator**: on the tape mark, controllers in hand. Never presses A or Home.
- **Keyboard person** (may be the spotter): types the commands below; never while the wearer is engaged.

## Operating rules until follower feedback exists (R1-R4)
- **R1** No squeeze until the spotter says **"ready"** (the follower's startup move has ended and the
  arm is still). This also applies after **every FAULT**: release → hand back to where you engaged →
  wait for "ready" → squeeze. The same FAULT twice in a row → **stop the session**.
- **R2** After any sender restart, restart the follower too, and the other way round. The sender
  **exits by itself** 5 s after the follower stops answering; that always means: restart both.
  With two arms, one sender per arm.
- **R3** `--confirm-follower-at-start-pose` means: R1/R2 followed, item 6 PASS today, and the spotter
  compared the arm with the start-pose values the sender prints (within about 10°; it sags 5-6°).
- **R4** Before the follower is stopped with `esc`, the wearer has **released the grip** (`HOLD`
  in the terminal). `esc` then moves the arm **by itself** back along its start path to the lowered
  pose and lets go: expected motion, do not e-stop it, keep clear and be ready to support the arm.

## Procedure (left arm)
1. Spotter: items 3, 4, 8 for today; arm power still **off**.
2. Keyboard person, operator PC (one terminal facing the spotter):
   `bash scripts/pico_env.sh python -m pico_teleop.sender --host <follower-ip> --arms left --confirm-follower-at-start-pose --log runs/arm-<date>-<n>-sender.jsonl > runs/arm-<date>-<n>-sender.out 2>&1 &`
   and `tail -f runs/arm-<date>-<n>-sender.out | grep -E "ENGAGED|released|FAULT|LINK|STOPPED|refused|\[left \]"`.
   It sends the start pose and **cannot engage** until the follower answers.
3. Spotter: workspace clear → release the e-stop → switch on the **left** PSU.
4. Keyboard person, follower PC: CAN up (`sudo bash scripts/canup.sh` if `ip -br link show can_fol_l`
   is not UP), then `bash scripts/run_teleop_remote_left.sh` (openarm-orboh). **The arm moves by
   itself** to the start pose (a few seconds, along waypoints).
5. Spotter: arm still, compare with the start pose → **"ready"**.
6. Wearer, first squeezes test signs only: **2 cm forward** → release; **2 cm left** → release;
   **2 cm up** → release. Spotter says each direction aloud ("forward OK" …). **Trigger untouched.**
6b. Wrist rotation (only after step 6 passes and go/no-go item 10 is PASS): stop with `esc`, restart
   the sender **with `--orientation`** and the follower (R2). One axis per squeeze, **about 10°,
   slowly**, in this order: yaw, pitch, roll: turn → hold → turn back → release. The spotter calls
   each ("yaw OK"). Trigger untouched. Up to 20° only after all three pass.
7. Only after all three: small slow motions **≤ 10 cm** per squeeze, one arm. Larger ranges only
   as a planned, logged change of this rule.
8. End: wearer releases the grip (`HOLD`) → spotter says **"esc"** → keyboard person presses `esc`
   in the follower terminal → arm returns and goes limp (R4) → the sender exits by itself (LINK DOWN,
   then SENDING STOPPED after 5 s) → spotter: PSU off / e-stop pressed.
9. Record (new `log/log-NNN.md` + row in `log/log.md`): run files, operator, spotter, positions,
   today's go/no-go column, directions confirmed (per axis), FAULT list, **deviations from steps 6-8**,
   how the follower ended, final arm/PSU state; update the README status row if the scope changed.

## Spotter card
| You see | Do first | Then |
|---|---|---|
| arm moves when the grip is open, or jumps (exceptions: the follower's startup move; the `esc` return move (R4); the record 'a' key; the **pre-move** after Enter (10°/s, log-028); all announced) | **emergency stop** (or PSU off) | support the arm or keep clear: with power off it **drops** |
| `FAULT` in the terminal | call "FAULT" (the arm is frozen, safe) | wearer releases, hand back; you say "ready" before the next squeeze (R1). Same FAULT twice → stop |
| noise, shaking, sudden speed | call "release" | if it does not stop: emergency stop |
| `LINK DOWN` / `SENDING STOPPED` | nothing moves (the receiver holds) | R2: restart both; never restart only one |
| `echo of packets we did not send` | **emergency stop** | another sender drives this arm; find and stop it |
| the wearer's hand leaves the headset's view while engaged | call "release" | a hidden controller is not detected (log-007) |
| wearer squeezes before "ready" | call "release" | repeat R1 |
| (with `--orientation`) the wrist turns while the hand's position is still | expected only if the wearer turned the hand (it may finish turning ~0.5 s later, 90°/s limit); otherwise call "release" | — |
| (with `--orientation`) fingers or the flange swing toward the table or the arm | call "release" | if it does not stop: emergency stop |
| `WALL: …` (log-030) | the arm stops at the workspace edge and **holds** (not a FAULT; it may still finish up to 10° of joint motion toward its last target). joint_1 may swing up to **120°** from the start: keep the area to the robot's left/front clear | nothing; the wearer moves back. **The wearer cannot see the message in the headset: the spotter calls "wall, move back"** when the arm stops and the wearer keeps pushing (> 2 s). Wall time and slip are in the run log |
| `LOCKED` in the terminal | the sender refuses to engage until restarted (same FAULT twice in 30 s) | stop: release, `esc`, restart both (R2); log why it repeated |
| anything unexpected | stop the session | log it before resuming |

## Pilot recording with the PICO (left arm, log-024)
Roles: operator (headset) **and a separate spotter at the e-stop**; keyboard person may be the spotter.
Before: today's go/no-go column filled; the arm's sweep area clear for a joint_1 swing of up to 120°.
1. Input check: `bash ~/projects/PICO-controller/scripts/check_input.sh` → expect `rate ~50-80 Hz | L ok`.
2. Terminal A: `bash ~/projects/PICO-controller/scripts/gate4_sender.sh pilot-pico-<n>` → expect the
   start-pose line with sha `2ec31fb82bf0`, then `HOLD`.
3. Spotter: **"arm may move"** → left arm power supply (PSU) on.
4. Terminal B: `bash ~/projects/PICO-controller/scripts/pilot_record.sh pico` → **the arm moves by itself**
   to the start pose; spotter compares with the printed start pose, then says "ready".
5. Episodes: → starts, `a` ends and resets (**the arm moves by itself**), 5 episodes, `esc` at the end
   after the grip is released (R4). Note "grasp OK / placed OK" per episode.
6. **Stop rules:** the first `LOCKED` → stop the block (`esc`, PSU off, Ctrl-C terminal A), log why.
   Anything unexpected → e-stop. Never continue without the spotter's "ready".
7. After: PSU off; log-NNN with per-episode result, FAULTs, smallest margin to the joint_1 limit.

## One-arm sessions without recording: R1 position, W1 / R2 wrist (log-026)
Script: `bash ~/projects/PICO-controller/scripts/arm_session.sh <left|right> <position|wrist>`.
It shows the `[sender]` start-pose line: check the sha (left `2ec31fb82bf0`, right `a3b97b8e9d66`).
| | left | right |
|---|---|---|
| power supply | left PSU | **right PSU** |
| CAN | `can_fol_l` | `can_fol_r` |
| follower (terminal B) | `cd ~/projects/openarm-orboh && bash scripts/run_teleop_remote_left.sh` | `… run_teleop_remote_right.sh` (port 5701/5711) |
| start pose | j1 +64.4, j4 95.9, j7 −68.8 | j1 −59.6, j4 87.2, j7 66.7 |
Before: no leader_agent running (`pgrep -af leader_agent` empty), go/no-go incl. item 10 PASS today.
- **R1 (right, position):** 2 cm forward → release; 2 cm left → release; 2 cm up → release. Spotter calls each.
  PASS = every direction right by eye; any wrong direction or motion without a squeeze → e-stop, stop, log.
- **W1 / R2 (wrist):** first a 2 cm forward squeeze; then yaw, pitch, roll **10° only**, slowly, one per
  squeeze: turn → hold 2 s → turn back → release. Before each pitch: fingers ≥ 10 cm above the table; pitch last.
  **No 20° turns**: offline, 20° FAULTs on left pitch down, left yaw left, right pitch down, right yaw right
  (joint_2 / joint_7 within 3° of a limit; with `--orientation` the limit avoidance is not active).
- Expected at the edge: `WALL: …` (log-030/033; arm holds, not a FAULT: move back). After `WALL: hand turned faster
  than the gripper` the gripper keeps turning ≤ 90°/s for a moment (it catches up to 40° behind the hand). Real FAULTs
  (tracking jumps): release; "ready".
- **Stop rules:** the same FAULT twice or `LOCKED` → stop the session; wrong direction → release, stop, log.
- PASS is judged by the spotter's eye (no follower feedback); the log gives the commanded values only.

## Collection with the GUI (log-038/039)
`bash ~/projects/PICO-controller/scripts/gui.sh` → http://localhost:8090. Three people: page operator (PC), spotter
(e-stop + power switch, confirms ② by name), wearer / leader operator. The page follows this runbook's order and
refuses steps out of order; it is **not** an emergency stop. Today's go/no-go column must be all PASS first.
After every recording: ④ Stop teleop, then ① again (one recording per teleop start, R2).
Do not close the GUI while a leader agent runs and nobody holds the leader arm.
