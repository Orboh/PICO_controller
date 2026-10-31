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
| 2 offline mapping / IK | done offline (tests + `evidence/gate2`, all PASS) | live check of axis signs; measure the real grasp point (`--tcp-offset-m`); gripper open angle (-60° not re-measured); view a run in Meshcat |
| 3 fake receiver | done offline (real receiver class, localhost) | — |
| 4 recording dry run | **blocked**: needs the follower PC + cameras | run `lerobot-record` with the PICO sender (log-006) |
| (all) | | decide: grip held ≥ 0.3 s to engage (trigger pulls the grip by accident, log-007) |
| 5 hardware | **blocked** | follower-pose feedback for the anchor (log-006); safety protocol prerequisites (below); right-arm zero/sign check |

Nothing in this repository has moved an arm.

## Controls (sender, gate 3 onward)

Left controller drives the left arm, right controller the right arm. Stand **behind the robot,
facing the same way it faces**, then recenter the headset (forward for you = forward for the arm).

| Input | Effect | When |
|---|---|---|
| **Grip, squeeze firmly (> 0.6)** | ENGAGE: the arm follows your hand's *motion* from where it is now, at half scale. The arm does not jump to your hand. | only from HOLD, and only after the grip was seen open once since start (grip already held at start = nothing happens) |
| **Grip, open fully (< 0.4)** | HOLD: the arm stops at the last sent target and stays there. Your hand can move freely. | ENGAGED |
| Grip half-squeezed (0.4 to 0.6) | nothing changes (keeps the current state) | always |
| **Trigger** | gripper: released = open (-60°), fully pulled = closed (0°). **Soft takeover:** after each engage the gripper does not move until the trigger "meets" its current opening (within 3°): e.g. after start the gripper is closed, so pull the trigger fully first, then let go to open. | ENGAGED only; in HOLD and FAULT the gripper keeps its angle |
| A/B/X/Y, menu, sticks | nothing in the sender. **A toggles sending in the PICO app: pressing it stops all input (the arm FAULTs).** | — |
| **Ctrl-C** on the sender PC | stop sending. The receiver holds the last pose after 250 ms (the arm stays powered). | always |

**There is no stop button on the controllers.** To stop the arm moving: open the grip. To stop
everything: the spotter presses the emergency stop / switches off the arm PSU (safety protocol §1).

**Keep both controllers in front of you while engaged; open the grip before a hand leaves the view.**
A hidden controller is NOT detected: its reported position just stays put, then catches up when it
is seen again (log-007), and the arm follows that catch-up (speed-limited).

**FAULT:** the arm freezes and ignores your hand. You cannot see this inside the headset (no
vibration or sound yet), so **the spotter watches the sender terminal and calls out "FAULT"**.
To recover: open the grip fully, move your hand back toward the middle, squeeze again.

| Message in the terminal | Plain meaning | What to do |
|---|---|---|
| `input stale` / `pose zero` / `pose frozen` / `grip/trigger out of range` | tracking or the headset stream was lost | check the headset is on and the controller is in view; release, squeeze |
| `link lost: no echo from the receiver` | the arm PC stopped answering | spotter checks the arm PC; do not re-squeeze until it is back |
| `target unreachable` | the hand asked for a point the arm cannot reach | release, bring the hand back toward you, squeeze |
| `near singular` | the arm is almost straight | same as above |
| `IK jumped … deg in one cycle` | the solver tried to flip the arm's posture | same as above |
| `joint_N would move … deg from where you engaged` | big posture change in one squeeze (limit 45°) | release, squeeze again: it re-anchors and allows another 45° |
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

1. **Start the PC Service** (any terminal, no sudo; stays in the background):
   ```bash
   cd /opt/apps/roboticsservice && \
   LD_LIBRARY_PATH=/opt/apps/roboticsservice:/opt/apps/roboticsservice/lib:/opt/apps/roboticsservice/SDK/x64 \
   nohup ./RoboticsServiceProcess > /tmp/roboticsservice.log 2>&1 &
   ss -tln | grep -E '63901|60061'     # ready when both lines appear
   ```
   (On this PC it was already running on 2026-10-31.)
2. **Connect the headset:** PC and PICO on the same Wi-Fi (in the office: `orboh`). Find this PC's IP with `hostname -I`
   (DHCP: check every session). In the XRoboToolkit app, type it in **"PC Service:"**; wait for
   **`Status: WORKING`**. Then tick **Controller** and turn **sending on** (Send, or press A once).
   **A toggles sending: do not press A again.** Close the PC Service's 3D demo window (a second
   reader gets zeros). Keep the headset on: about 8 s after taking it off it sleeps and the stream stops.
3. **Run the guided diagnostic** (about 1–2 min; Enter starts each phase, so put the headset on first):
   ```bash
   bash ~/projects/PICO-controller/scripts/pico_env.sh python -m pico_teleop.diag_input
   # or one phase at a time (what worked on 2026-10-31):
   bash ~/projects/PICO-controller/scripts/pico_env.sh python -m pico_teleop.diag_input \
       --no-prompt --phase left --phase-seconds 30 --lead-in-s 8
   ```
   Phases: left only → right only → hide left → hide right → headset off. Follow the printed text.
   Never press the PICO/Home system button. Menu buttons are reported, not required.
4. **Read the result** at the bottom: `GATE 1 (LIVE, recorded to runs/diag-<time>.jsonl): PASS|FAIL`.
   Each FAIL line names the check. Every invalid / frozen / stale interval is listed as `event:`.

On FAIL:
- `ts=0` and every pose zero: the headset is not connected (step 2), or another program reads the PICO.
- `moved` too small: move further (10 cm or more). `swept` too small: release fully, then squeeze fully.
- `stayed still` failed: the other controller was moving, or left and right are swapped. Report this.
- `loss: no interval`: the controller was not hidden long enough. Note what the SDK did instead.
- Rerun once. If it fails again, keep the `runs/` file and write it up (below).

After every run: write a log (`log/log-NNN.md` plus a row in `log/log.md`) with the command, the git
commit (`git log -1 --oneline`), the printed summary and the `runs/` path. Re-score later with
`... python -m pico_teleop.diag_input --replay runs/diag-<time>.jsonl` (prints `REPLAY`).

## Before any real-arm run (gate 5)

From `notion/OpenArm 安全プロトコル…md` (archived copy, 2026-10-30) and Kento's request §9:
an Orboh member beside the arm (the PICO wearer cannot see it); emergency stop **confirmed to exist**
(protocol TODO#1 is open) and within reach; the arm PSU switches located and labelled (power cycle is
the only sure way to go limp); workspace clear (`connect()` moves the arm by itself for about 2.2 s);
start pose verified; one arm, small and slow motions first. Not started; nothing here has moved an arm.
