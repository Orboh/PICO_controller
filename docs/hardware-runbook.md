# Gate 5 — supervised real-arm sessions with the PICO (runbook + spotter card)

Status: approved supervised left-arm tests on 2026-10-31: session 2 (directions) and session 3 (gripper
by trigger), operator Kota-san, spotter Suraj (log-010/011/014). **Covered**: left arm, position control,
gripper by trigger, TCP offset 0.1025 (assumed), follower checkout `openarm-orboh` `feat/takeei-wood-l1`
@ e98e987, lerobot `feat/bi-openarm-soft-move-to` @ a720ec19. **Not covered**: right arm, both arms,
**wrist rotation (`--orientation`): do not use on the arm before go/no-go item 10**, recording, a
measured TCP, the default branches.
Source rules: `notion/OpenArm 安全プロトコル…md` (archived copy), Kento's request §8-9.
If anything surprises anyone: **stop** (spotter card below).

## Go / no-go — fill a NEW dated column every session day (all PASS, else no session)
| # | Item | Pass condition | Kind | Owner | 2026-10-31 |
|---|---|---|---|---|---|
| 1 | Gate 1 live | `GATE 1 … PASS` on record (log-007) | one-time per headset/app version | Suraj | PASS |
| 2 | Axis signs, no arm | dry-run table in README; plus the 2 cm one-axis check on the arm (step 6) | one-time per code change in `frames.py` | Suraj | PASS (left "left" row to redo cleanly) |
| 3 | Emergency stop | located, pointed at, **pressed once with the arm off**, within the spotter's reach | **daily** | spotter (name) | done, Kento-san + Suraj (not "pressed once" on record) |
| 4 | PSU switches | which switch = which arm, labelled, reachable by the spotter | **daily** | spotter (name) | done |
| 5 | Arm and side | left arm only until the right arm's zero/sign is checked | per plan | Suraj + Kento | left |
| 6 | Start pose and checkout | sender prints the start-pose sha = `sha256sum` of the follower's file; follower checkout branch + commit as above (or a newer one Kento names) | **daily** | Suraj | PASS (sha 2ec31fb82bf0) |
| 7 | Feedback | `proposals/lerobot-follower-feedback.patch` applied **or** Kota/Kento OK for R1-R4 **in writing** (link here) | until the patch lands | Kota / Kento | session 2 (the counted test): Kota-san operated and gave the OK; written in Slack 12:59 https://orbohworkspace.slack.com/archives/C0C5F68JX8R/p1793419165855809 (general wording; ask him to confirm the scope). Session 1 had no approval and does not count |
| 8 | Standing mark | tape outside the arm's reach for the wearer; workspace clear | **daily** | spotter (name) | done |
| 9 | Grasp point | `--tcp-offset-m` measured or 0.1025 accepted | one-time | Suraj | 0.1025 accepted |
| 10 | Wrist-rotation dry run | README "Wrist-rotation dry run" table PASS for both arms (log it) | before the first `--orientation` arm session; again after code changes | Suraj | pending |

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
| arm moves when the grip is open, or jumps (exceptions: the follower's startup move; the `esc` return move (R4); the record 'a' key; all three announced) | **emergency stop** (or PSU off) | support the arm or keep clear: with power off it **drops** |
| `FAULT` in the terminal | call "FAULT" (the arm is frozen, safe) | wearer releases, hand back; you say "ready" before the next squeeze (R1). Same FAULT twice → stop |
| noise, shaking, sudden speed | call "release" | if it does not stop: emergency stop |
| `LINK DOWN` / `SENDING STOPPED` | nothing moves (the receiver holds) | R2: restart both; never restart only one |
| `echo of packets we did not send` | **emergency stop** | another sender drives this arm; find and stop it |
| the wearer's hand leaves the headset's view while engaged | call "release" | a hidden controller is not detected (log-007) |
| wearer squeezes before "ready" | call "release" | repeat R1 |
| (with `--orientation`) the wrist turns while the hand's position is still | expected only if the wearer turned the hand (it may finish turning ~0.5 s later, 90°/s limit); otherwise call "release" | — |
| (with `--orientation`) fingers or the flange swing toward the table or the arm | call "release" | if it does not stop: emergency stop |
| anything unexpected | stop the session | log it before resuming |
