# Collection website: setup and operation (leader arm or PICO)

## Setup (once per PC)
```bash
cd ~/projects/PICO-controller
uv venv --python 3.10 .venv && uv pip install --python .venv/bin/python "pin==4.1.0" "numpy<2.3" pyzmq pyyaml pytest matplotlib "nicegui>=2,<3"
bash scripts/test.sh -q        # offline tests, no hardware
```
Also needed: the PICO setup (`docs/pico-working-setup.md`), openarm-orboh (recorder, leader agents, pose sets),
lerobot with its venv (recorder, RealSense).

## Start
```bash
bash ~/projects/PICO-controller/scripts/gui.sh     # open http://localhost:8090 on this PC (only this PC can open it)
```
English / 日本語: the button at the top right. Only one browser tab controls; other tabs watch ("Take control here").

## People
| Role | Does |
|---|---|
| Page operator (at the PC) | presses the buttons in order, writes the episode notes |
| Spotter (at the arm) | e-stop and power switch; types their name at ② and says "arm may move"; calls "wall, move back" |
| Operator (headset or leader arm) | teleoperates; releases the grip before the end |

## The page, top to bottom
- **Card 0 — Before you start: connections** (refreshed every 3 s, a toast when one is lost or back):
   PICO: PC on Wi-Fi `orboh`, PC Service running, 3D demo closed, headset answers, PICO data (button "Test PICO input");
   leader: leader arm CAN up; both: follower CAN up, cameras (button), today's go/no-go all PASS, no other sender.
   Red items show what to do. Fix them before ①.
- **Card 1 — Setup**: method (PICO → follower / Leader arm → follower), arm, **stage**, episodes, seconds, operator name, task
   (same text for both methods); PICO: smooth motion (on), wrist rotation, pre-move.
   - **dry** = 2 throw-away episodes to check everything (dataset `…_tmp`);
   - **gate** = 10 episodes for review (dataset `Orboh/openarm_<method>_<arm>_gate_<time>`); afterwards the
     **Gate review** card asks for the reviewer's name and OK;
   - **session** = the real collection (`…_session_<time>`), only after a reviewed gate for the same method and arm.
- **Card 2 — Run** ("Now: …" shows the next step; grey buttons cannot be pressed yet):
   ① Start teleop → ② spotter name + "arm may move, power ON" → ③ Start recording (**the arm moves** to the start
   pose) → ▶ Next episode / ↺ Discard + re-record / ⟲ Return to start (arm moves) / ■ End recording (**not an
   e-stop**) → ④ Stop teleop. One recording per ①; for another, ④ then ① again.
   - **Live status** (PICO): HOLD / ENGAGED / FAULT / LOCKED, link, input rate, facing, BOUNDARY (wall) and its time,
     start-pose sha; recording episode and its time.
   - **Episodes**: grasp / placed / note and boundary time, saved at once to `runs/gui-<method>-<arm>-<time>-notes.json`
     (with settings, spotter, run folder, dataset).
- **Logs**: session log, recorder output, teleop events.

## What the page refuses (on purpose)
Start without an operator name, with go/no-go not all PASS, or with another sender running; a session without a
reviewed gate; recording before ②; a wrong start pose; End / Return / Discard while the operator squeezes; Next after
LOCKED; for the leader arm: End / Return / ④ without "the leader arm is held".

## When something goes wrong
| You see | Do |
|---|---|
| red banner "ANOTHER SENDER … E-STOP" | e-stop first, then find the other program |
| red banner "TELEOP / RECORDER EXITED" | spotter watches the arm; release; End recording; the episode is flagged |
| LOCKED | release → End recording → ④ → ① again |
| a connection turns red | stop the episode (End recording), fix it (the card says how), start again from ① |
| closing the page / Ctrl-C of `gui.sh` | the recorder ends (Esc) and a PICO sender stops; a **leader agent keeps running**: hold the leader arm and stop it in a terminal |

## Where the data goes
Run folder `~/collections/<UTC>-pilot-<method>/` (console log, manifest), dataset
`~/.cache/huggingface/lerobot/<repo id>_<time>` (local only, nothing is pushed), notes `runs/gui-…-notes.json`,
gate reviews `runs/gui-reviews.json`, PICO sender log `runs/gui-…-sender.jsonl`.
