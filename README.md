# PICO-controller — PICO 4 Ultra controllers → OpenArm follower

Reads the PICO controllers, turns hand motion into follower joint angles (IK), validates every packet,
and sends it to the existing follower receiver `openarm_leader_remote` (lerobot fork) on port 5701
(right) / 5702 (left). The video path and the receiver are not rebuilt here.
Goal and gates: `goal.md`. Why each choice was made: `log/log.md`. Background: `notion/`.

```
PICO ─Wi-Fi─ PC Service :63901 ─ xrobotoolkit_sdk :60061 ─ pico_teleop (one reader) ─ZMQ 5701/5702─ openarm_leader_remote ─ follower
```

## Status

| Gate | State |
|---|---|
| 1 input-only diagnostic | tool + 12 offline tests done; **live run pending (needs the headset)** |
| 2 offline mapping / IK | in progress |
| 3 fake receiver | not started |
| 4 recording dry run | not started |
| 5 hardware | **blocked**: safety protocol prerequisites (below) |

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
2. **Connect the headset:** PC and PICO on the same 5 GHz Wi-Fi. Find this PC's IP with `hostname -I`
   (DHCP: check every session). In the XRoboToolkit app, type it in **"PC Service:"**; wait for
   **`Status: WORKING`**. Keep the headset on: taking it off puts it to sleep and stops the stream.
3. **Run the guided diagnostic** (about 1–2 min; Enter starts each phase, so put the headset on first):
   ```bash
   bash ~/projects/PICO-controller/scripts/pico_env.sh python -m pico_teleop.diag_input
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
