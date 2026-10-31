# PICO-controller — PICO 4 Ultra controllers → OpenArm follower

Reads the PICO controllers, turns hand motion into follower joint angles (IK), validates every packet,
and sends it to the existing follower receiver `openarm_leader_remote` (lerobot fork) on port 5701
(right) / 5702 (left). The video path and the receiver are not rebuilt here.
Goal and gates: `goal.md`. Why each choice was made: `log/log.md`. Background: `notion/`.

```
PICO ─Wi-Fi─ PC Service :63901 ─ xrobotoolkit_sdk :60061 ─ pico_teleop (one reader) ─ZMQ 5701/5702─ openarm_leader_remote ─ follower
```

## Status

| Gate | State | Still needed before hardware |
|---|---|---|
| 1 input-only diagnostic | **PASS live** 2026-10-31, all 5 phases (log-007, `evidence/gate1`) | — (finding: a hidden controller is not detectable) |
| 2 offline mapping / IK | done offline (tests + `evidence/gate2`, all PASS) | measure the real grasp point (`--tcp-offset-m`); gripper open angle (-60° not re-measured); view a run in Meshcat |
| 3 fake receiver | done offline, and **online PASS** with live PICO → real port 5701 → real receiver (no robot, log-009) | — |
| 4 recording dry run | **blocked**: needs the follower PC + cameras | run `lerobot-record` with the PICO sender (log-006) |
| 2 axis signs (no arm) | **PASS** both arms 2026-10-31 (log-008) | — |
| 5 hardware | **first supervised session PASS** 2026-10-31: left arm, directions confirmed by the spotter, clean esc stop (log-010) | right arm; gripper by trigger; follower-feedback patch (Kento) |

First real-arm sessions: 2026-10-31, left arm, supervised (log-010).

## Controls (sender, gate 3 onward)

Left controller drives the left arm, right controller the right arm. Stand **behind the robot,
facing the same way it faces**, then recenter the headset (forward for you = forward for the arm).

| Input | Effect | When |
|---|---|---|
| **Grip, squeeze and keep holding** | after **0.3 s** of holding (the terminal prints `ENGAGED`): ENGAGE, the arm follows your hand's *motion* from where it is now, at half scale. The arm does not jump to your hand. A short accidental squeeze (e.g. while pulling the trigger) does nothing. | only from HOLD, only with the link up (not waiting/LINK DOWN), and only after the grip was seen open once since start (grip already held at start = nothing happens) |
| **Grip, open fully (< 0.4)** | HOLD: no new targets are sent; the arm finishes the last small step (≤ 0.9° per joint, one 20 ms cycle) and stays there. Settling on the real arm: to be measured in the first 2 cm test. Your hand can move freely. | ENGAGED |
| Grip half-squeezed (0.4 to 0.6) | nothing changes (keeps the current state) | always |
| **Trigger** | gripper: released = open (-60°), fully pulled = closed (0°). **Soft takeover:** after each engage the gripper does not move until the trigger "meets" its current opening (within 3°): e.g. after start the gripper is closed, so pull the trigger fully first, then let go to open. | ENGAGED only; in HOLD and FAULT the gripper keeps its angle |
| A/B/X/Y, menu, sticks | nothing in the sender. **A toggles sending in the PICO app: pressing it stops all input (the arm FAULTs).** | — |
| **Ctrl-C** on the sender PC | stop sending. The receiver holds the last pose after 250 ms (the arm stays powered). Before starting the sender again: restart the follower (runbook R2). | always |

**There is no stop button on the controllers.** To stop the arm moving: open the grip. To stop
everything: the spotter presses the emergency stop / switches off the arm PSU (safety protocol §1).

**Keep both controllers in front of you while engaged; open the grip before a hand leaves the view.**
A hidden controller is not flagged by the PICO (its position just stays put). When it is seen again it
jumps: the sender FAULTs on jumps faster than 3 m/s, and on a desired TCP more than 15 cm ahead of the
arm target. A slower drift below both limits would be followed (speed-limited); not measured live.

**FAULT:** the arm freezes and ignores your hand. You cannot see this inside the headset (no
vibration or sound yet), so **the spotter watches the sender terminal and calls out "FAULT"**.
To recover: open the grip fully, bring the hand back to where it was when you engaged (or closer to your body), hold the grip 0.3 s again. If the same FAULT repeats: stop and report.

| Message in the terminal | Plain meaning | What to do |
|---|---|---|
| `input stale` / `pose zero` / `pose frozen` / `grip/trigger out of range` | tracking or the headset stream was lost | check the headset is on and the controller is in view; release, squeeze |
| `target unreachable` | the hand asked for a point the arm cannot reach | release, bring the hand back toward you, squeeze |
| `near singular` | the arm is almost straight | same as above |
| `IK jumped … deg in one cycle` | the solver tried to flip the arm's posture | same as above |
| `hand jumped at … m/s` | the controller position jumped (tracking lost and caught up) | keep the controller in view; release, squeeze |
| `hand is … cm ahead of the arm target` | your hand moved faster than the arm may follow; the 15 cm is measured at the gripper tip (hand × 0.5) | release, move slower, squeeze |
| `LINK DOWN` | no echo from the arm PC for 1 s (it may be saving an episode). The arm holds; the sender keeps sending the held pose **for 5 s, then stops and exits** (`SENDING STOPPED`) | if `link back` comes within 5 s: release and squeeze on "ready". After `SENDING STOPPED`: restart **both** follower and sender (runbook R2) |
| `echo of packets we did not send` | another sender (e.g. leader_agent) uses the same arm port | spotter: **emergency stop first** (the other sender may still drive the arm), then find and stop the other sender |
| `joint_N would move … deg from where you engaged` | big posture change in one squeeze (limit 60°) | release, squeeze again: it re-anchors and allows another 60°; if it repeats, stop |
| `validator: …` | a packet was unsafe (non-finite, over a limit, too big a step) and was not sent | stop the session and report it |

The sender prints the state of each arm once per second and every event as it happens.

## Setup (once per PC)

```bash
cd ~/projects/PICO-controller
uv venv --python 3.10 .venv && uv pip install --python .venv/bin/python "pin==4.1.0" "numpy<2.3" pyzmq pyyaml pytest matplotlib
bash scripts/test.sh -q          # offline tests, no hardware
```
The SDK is the py3.10 build in `~/Desktop/XRoboToolkit-PC-Service-Pybind` (commit 75cb113).
`scripts/pico_env.sh` puts it on the path and refuses to run if the PC Service is down.

## Gate 1 — input-only diagnostic (nothing is sent to any robot)

Preflight (all must be true):
- The follower receiver (`run_teleop_remote_*.sh`) and any `leader_agent` / PICO sender are **not running**.
  The diagnostic refuses to start if it finds one, but also check the arm PC by eye.
- Only one program reads the PICO: a second reader silently gets zeros (Kento's request §8).

1. **PC Service** (no sudo). If `ss -tln | grep -E '63901|60061'` shows both ports it is running:
   skip the start. Otherwise start it:
   ```bash
   cd /opt/apps/roboticsservice && \
   LD_LIBRARY_PATH=/opt/apps/roboticsservice:/opt/apps/roboticsservice/lib:/opt/apps/roboticsservice/SDK/x64 \
   nohup ./RoboticsServiceProcess > /tmp/roboticsservice.log 2>&1 &
   ```
   **Close its 3D demo** (it is a second reader): `pgrep -af RobotLinuxDemo` must print nothing;
   if it does, close that window or `kill <pid>`.
2. **Network:** switch the PC to Wi-Fi **`orboh`** and read its `192.168.0.x` address from
   `hostname -I` (other addresses there are docker/USB; ignore them). The PICO must be on `orboh` too.
3. **App:** "PC Service:" = that `192.168.0.x` address → wait for `Status: WORKING` → tick
   **Controller** → turn sending on (Send in the app). Confirm with the 3-s check below. **From then on
   never press A** (A toggles sending off). Never short-press Home (leaves the app); recentering is a
   long press of Home (PICO default; confirm on your unit) and is the only Home use allowed.
4. **Check data** (prints `rate ~80 Hz | R ok L ok` when good):
   ```bash
   cd ~/projects/PICO-controller && bash scripts/pico_env.sh python -c "import time;from pico_teleop.xr_input import SdkSource,pose_status;s=SdkSource();t=time.monotonic();n=set()
   while time.monotonic()-t<3: f=s.read();n.add(f.t_device_ns);time.sleep(0.005)
   s.close();print('rate ~%.0f Hz'%(len(n-{0})/3),'| R',pose_status(f.right.pose),'L',pose_status(f.left.pose))" 2>&1 | grep rate
   ```
   `rate ~0 Hz` / `zero`: sending is off (step 3), the demo is open (step 1), or the headset slept.
   Still zero after all three: restart the service (`pkill -f RoboticsServiceProcess`, then step 1)
   and reconnect the app. This fixed it on 2026-10-31.
5. **Keep the headset on** from here (off for about 8 s = sleep = stream stops). A second person reads the
   phase text below aloud and runs the commands; the wearer only says "go".
6. **One phase per run** (8 s lead-in after the data check, then the phase):
   ```bash
   D="bash scripts/pico_env.sh python -m pico_teleop.diag_input --no-prompt --lead-in-s 8"
   $D --phase left        --phase-seconds 30   # left only; right lies still
   $D --phase right       --phase-seconds 30   # right only; left lies still
   $D --phase left_loss   --phase-seconds 20   # hold left in view 5 s, behind your back 7 s, back in view
   $D --phase right_loss  --phase-seconds 20   # same with right
   $D --phase headset_off --phase-seconds 20   # headset on 4 s, off and laid down 10 s, back on
   ```
   left/right phase, in this order: 10 s moving about 10 cm forward/back, left/right, up/down → grip fully
   twice → trigger fully twice → X then Y (left) / B only (right).
7. **Result:** each run ends `GATE 1 (LIVE phases <name>, …): PASS|FAIL`. Gate 1 = all five PASS.
   Expected PASS lines: left 8 checks, right 7, each loss phase 1 ("stream stayed ≥ 30 Hz": the loss
   itself is only reported, because a hidden controller is not detectable), headset_off 1 ("stream went
   stale": the stop is the expected result).

On FAIL: `moved` too small → move further; `swept` too small → open fully, squeeze fully; `stayed
still` → the other controller moved or sides are swapped (report it); `no gap > 0.25 s` → Wi-Fi gap, rerun
once. Re-score any file: `bash scripts/pico_env.sh python -m pico_teleop.diag_input --replay runs/<file>`.

After the runs (checklist): gzip the passing files into `evidence/gate1/` · write the next
`log/log-NNN.md` (command, `git log -1 --oneline`, the PASS lines, file names) · add its row to
`log/log.md` · update the README status row. Sign-off for gate items: Suraj.

## Axis-sign check without an arm (runbook go/no-go item 2)

The sender in `--dry-run` creates **no socket at all** (nothing can reach 5701/5702) and skips the
receiver-echo gate, so you can engage with no follower. Preflight anyway: `ss -tln | grep -E ':570[12]'`
prints nothing, and no `run_teleop_remote_*` runs on any PC. Recenter facing the direction that will be
"robot forward". Then, one arm at a time:
```bash
cd ~/projects/PICO-controller && bash scripts/pico_env.sh python -m pico_teleop.sender --dry-run --arms left
```
(no `--host`, no `--confirm…`). Squeeze the left grip and hold it; move the hand **10 cm**, hold still 2 s,
read the once-per-second line `TCP moved x… y… z… cm` (robot frame: x forward, y left, z up; half scale):

| Hand move (10 cm) | Expected on that line |
|---|---|
| forward (away from you) | x ≈ **+5**, y and z within ±1 |
| left | y ≈ **+5**, x and z within ±1 |
| up | z ≈ **+5**, x and y within ±1 |

Release between moves (it re-anchors) and **release before bringing the hand back**. Repeat with
`--arms right`. Left/right words are easy to confuse: if a sign looks wrong, check the geometry first
(hold both controllers in front of you: the right one must read at XR +x of the left one, log-008) and
**never change the `frames.py` matrix from a left/right report alone**. Ctrl-C ends it. Any wrong sign or
axis → stop and report; do not go near an arm. Log it like gate 1 (runbook item 2 → PASS, owner Suraj).

## Before any real-arm run (gate 5)

From `notion/OpenArm 安全プロトコル…md` (archived copy, 2026-10-30) and Kento's request §9:
an Orboh member beside the arm (the PICO wearer cannot see it); emergency stop **confirmed to exist**
(protocol TODO#1 is open) and within reach; the arm PSU switches located and labelled (power cycle is
the only sure way to go limp); workspace clear (`connect()` moves the arm by itself for about 2.2 s);
start pose verified; one arm, small and slow motions first. First supervised left-arm sessions: 2026-10-31 (log-010, log-011); procedure: `docs/hardware-runbook.md`.
