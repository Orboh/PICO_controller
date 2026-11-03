# How to run

This page says what to run, in which order, and which page has the details. It does not replace the safety rules:
for anything that powers an arm, **`docs/hardware-runbook.md` governs** (with the OpenArm safety protocol, archived in
`notion/`). If this page and the runbook disagree, the runbook wins.

## 0. What runs where
| Piece | Where | Repo |
|---|---|---|
| PICO sender (headset → joint targets), collection website, tools | operator PC | this repo, `~/projects/PICO-controller` |
| PICO PC Service + SDK | operator PC | `/opt/apps/roboticsservice`, SDK `~/Desktop/XRoboToolkit-PC-Service-Pybind` |
| Follower receiver, recorder (`lerobot-record`), CAN, pose sets, URDF | follower PC (the collection PC today: the same PC) | `~/projects/openarm-orboh`, `~/projects/lerobot` (`feat/bi-openarm-soft-move-to`) |

This repo reads `~/projects/openarm-orboh` for the pose sets (`config/pose_sets/`) and the URDF; it must be there.

## 1. Setup (once per PC)
```bash
cd ~/projects/PICO-controller
uv venv --python 3.10 .venv && uv pip install --python .venv/bin/python "pin==4.1.0" "numpy<2.3" pyzmq pyyaml pytest matplotlib "nicegui>=2,<3"
bash scripts/test.sh -q          # offline tests, no hardware; expect all passed (a few skipped)
```
`scripts/pico_env.sh <cmd>` runs anything that needs the PICO SDK; it refuses when the PC Service is down.

## 2. Connect the PICO (every session)
Full steps: README "Gate 1" and `docs/pico-working-setup.md` ("Start, every day", "How to operate"). Short version:
1. PC Service running (`ss -tln | grep -E '63901|60061'` shows both ports), its 3D demo closed
   (`pgrep -af RobotLinuxDemo` prints nothing): only ONE program may read the PICO.
2. PC and headset on Wi-Fi `orboh` (or the tethering in the setup page); the app's "PC Service" = the PC's address,
   `Status: WORKING`, Controller ticked, sending on. **Never press A** afterwards (it turns sending off).
3. Check the input (reads only, sends nothing):
   ```bash
   bash scripts/check_input.sh          # expect: rate ~50-80 Hz | L ok (R ok)
   ```

## 3. Before any arm moves
Fill **today's go/no-go column** in `docs/hardware-runbook.md` (all PASS; item 5 says `collect: left|right|both`).
The website refuses to start without it. A second person (spotter) stands at the e-stop. Rules R1-R4 and the spotter
card are in the runbook.

## 4. Collect data: the website (main path)
```bash
bash scripts/gui.sh                  # open http://localhost:8090 on this PC (only this PC can open it)
```
Order on the page: card 0 checks green → card 1 settings (method, arm, stage dry → gate → session, operator, scene,
start-pose set) → ① Start teleop → ② spotter name + "arm may move", power on → Check cameras → ③ Start recording
(**the arm moves by itself** to the start pose) → ▶ Next / ↺ Discard / ⟲ Return / ■ End recording → ④ Stop teleop.
Full guide: `docs/collection-website.md` (refusals, recovery, where the data goes).

**Both arms with the PICO** additionally needs, on the follower PC, lerobot `feat/bi-openarm-leader-remote` (Kento's
two-arm receiver) and openarm-orboh `feat/bimanual-remote-teleop` (`proposals/openarm-orboh-feat-bimanual-remote-teleop.patch`).
Not yet run on the robot: dry stage first.

## 5. Terminal paths (no website)
Each needs the runbook's procedure; two terminals, the follower side started by a person.
| Goal | Operator PC | Follower PC |
|---|---|---|
| One-arm check without recording (R1 position, W1/R2 wrist) | `bash scripts/arm_session.sh left\|right position\|wrist` | `cd ~/projects/openarm-orboh && bash scripts/run_teleop_remote_<side>.sh` (CAN up first: `sudo bash scripts/canup.sh`) |
| Pilot recording, PICO | `bash scripts/gate4_sender.sh pilot-pico-<n>` | `bash scripts/pilot_record.sh pico [n]` (this repo; it calls openarm-orboh's recorder) |
| Pilot recording, leader arm | `bash scripts/leader_sender.sh left\|right` | `bash scripts/pilot_record.sh leader [n]` |

Without an arm (nothing sent):
```bash
bash scripts/pico_env.sh python -m pico_teleop.sender --dry-run --arms left    # axis-sign check (README)
bash scripts/wrist_dry_run.sh left|right                                       # wrist dry run (runbook item 10)
bash scripts/video_check.sh                                                    # PICO video toggle check
```

## 6. Stopping
- **Emergency: the hardware e-stop.** Not the page, not a key.
- Recorder: ■ End recording / `esc` = ordinary end: the arm **returns and goes limp**; the spotter supports it.
- PICO sender: Ctrl-C → the receiver **holds** the last pose. Leader agent: Ctrl-C makes the **leader arm go limp**: hold it first.
- Order: release the grip → end the recording → stop the teleop (R2, R4).

## 7. After a session
```bash
python3 tools/wall_report.py --from-log          # per-episode wall time + hand slip, ⚑ flagged PICO episodes
python3 tools/wall_report.py --from-log --json > runs/wall-report.json      # to send to Kento
# Kento's side experiment (in the lerobot venv): new dataset version with arm actions from the measured joints
~/projects/lerobot/.venv/bin/python tools/rebuild_actions.py <pico dataset dir> <new dir>_measact_k3 --lead 3
```
Notes per session: `runs/gui-<method>-<arm>-<time>-notes.json` (settings, start-pose set, branches + commits,
per-episode results). Run folders `~/collections/…`, datasets `~/.cache/huggingface/lerobot/…`. `runs/` is
git-ignored: keep a copy. Write a `log/log-NNN.md` entry for every robot session.

## 8. Troubleshooting
| Symptom | Fix |
|---|---|
| tests: `ImportError … libboost_python… _Py_fopen` (pinocchio) | a ROS library path shadows pinocchio: run with `env -u LD_LIBRARY_PATH bash scripts/test.sh` |
| tests / sender: `…/openarm-orboh/config/pose_sets/CURRENT` not found | openarm-orboh is not at `~/projects/openarm-orboh` |
| `pico_env.sh` refuses | start the PC Service (section 2) |
| page keys do nothing in the recorder | the recorder must start without a desktop display (the page does this; log-042) |
| PICO input `L ok` missing / rate 0 | sending off (A pressed), headset asleep, or a second reader (3D demo) |
| more | "When something goes wrong" in `docs/collection-website.md` and in `docs/hardware-runbook.md` |
