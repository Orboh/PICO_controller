# Gate 5 — first supervised real-arm session with the PICO (runbook + spotter card)

Status: **not yet allowed.** Every row of the go/no-go table must say PASS, signed by the named
owner, before the first squeeze. Source rules: `notion/OpenArm 安全プロトコル…md` (archived copy),
Kento's request §8-9. If anything surprises anyone: **stop** (spotter card below).

## Go / no-go (all PASS, else no session)
| # | Item | Pass condition | Owner |
|---|---|---|---|
| 1 | Gate 1 live | **PASS 2026-10-31** (log-007) | Suraj ✅ |
| 2 | Axis signs, no arm | **PASS 2026-10-31** both arms (log-008); redo the left-controller "left" row cleanly first | Suraj ✅ |
| 3 | Emergency stop | physically exists, location pointed at, pressed once with the arm off (protocol TODO#1) | Orboh member (name: ______) |
| 4 | PSU switches | which switch = which arm, labelled; spotter can reach it (protocol §1) | Orboh member (name: ______) |
| 5 | Arm and side | **left arm first** (right-arm zero/sign not checked on hardware) | Suraj + Kento |
| 6 | Start pose | the follower PC's `config/pose_sets/CURRENT` name = sender `--pose-set` (printed by both) | Suraj |
| 7 | Feedback | `proposals/lerobot-follower-feedback.patch` applied on the follower PC **or** Kento accepts operating rule R1-R3 below in writing | Kento |
| 8 | Standing mark | tape on the floor outside the arm's reach for the PICO wearer | Orboh member (name: ______) |
| 9 | Grasp point | `--tcp-offset-m` measured (ruler: link7 flange → between fingertips) or 0.1025 accepted | Suraj |

## Roles and hosts
- **Follower PC**: the PC with `can_fol_l` / `can_fol_r` (runs the receiver). Find its Wi-Fi IP
  with `hostname -I` and write it here: `__________`.
- **Operator PC**: runs the PICO PC Service and `pico_teleop.sender` (may be the same PC).
- **Spotter** (Orboh member): stands beside the arm, hand on the emergency stop, with the sender
  terminal turned toward them. Only the spotter says "ready".
- **Wearer**: on the tape mark, controllers in hand, never presses Home.

## Operating rules until feedback exists (R1-R3)
- **R1** Start order: follower first, wait for its alignment to finish, then the sender; or sender
  first, then follower; in both cases **no squeeze until the spotter says "ready"** (after the
  follower's startup move has ended and the arm is still).
- **R2** After **any** sender restart (Ctrl-C, crash, method switch to the leader arm), **restart
  the follower too**, and wait for "ready". Otherwise the first packet (start pose) snaps the arm back.
  And the other way round: if a follower was restarted, restart the sender. With two arms, a sender
  restart sends BOTH arms their start pose: restart both followers (or run one sender per arm).
- **R3** `--confirm-follower-at-start-pose` means: (a) R1/R2 were followed, (b) the pose-set names
  match (item 6), (c) the spotter compared the arm with the start-pose photo (within about 10°; it sags
  5-6° under gravity).

## Procedure (left arm, one axis at a time)
1. Follower PC: `sudo bash scripts/canup.sh`, then `bash scripts/run_teleop_remote_left.sh`
   (openarm-orboh). Clear the workspace: `connect()` moves the arm by itself for about 2.2 s.
2. Operator PC: `bash scripts/pico_env.sh python -m pico_teleop.sender --host <follower-ip> --arms left --scale 0.5 --confirm-follower-at-start-pose`.
3. Spotter checks the arm against the start-pose photo, then says **"ready"**.
4. First squeezes test signs only: squeeze, move the hand **2 cm forward**, release. Spotter
   confirms the gripper tip moved forward. Then left, then up. **Trigger untouched** (the open
   angle -60° is not re-measured).
5. Only after all three signs are right: small slow motions (≤ 10 cm), still one arm.
6. End: release grip → Ctrl-C the sender → stop the follower the way its runbook says
   (protocol §2 "after": all channels disabled, arm lowered, power off if a motor stays enabled).

## Spotter card
| You see | Do first | Then |
|---|---|---|
| arm moves when the grip is open, or jumps (exception: the record 'a' key, which slowly returns the arm to the start pose by design; announce it before pressing) | **emergency stop** (or PSU off) | support the arm or keep clear: with power off it **drops** |
| `FAULT` in the terminal | call "FAULT" (the arm is frozen, that is safe) | wearer opens the grip, hand back to the middle, squeeze again on your "ready" |
| noise, shaking, sudden speed | call "release" | if it does not stop: emergency stop |
| the sender crashed / closed | nothing moves (the receiver holds) | R2: restart the follower before starting the sender again |
| `LINK DOWN` and the follower is being restarted | **Ctrl-C the sender first** | its held pose would be the new follower's first target (R2) |
| `echo of packets we did not send` | **emergency stop** | another sender drives this arm; find and stop it |
| the wearer's hand leaves the headset's view while engaged | call "release" | a hidden controller is not detected (log-007) |
| anything unexpected | stop the session | log it (`log/log-NNN.md`) before resuming |
