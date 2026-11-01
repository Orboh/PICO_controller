# Collection website: setup and operation (leader arm or PICO)

## Safety first (read before touching the robot)
Source pages (Notion, read them fully once):
- **OpenArm 安全プロトコル（実機に触る前に必ず読む）** — https://app.notion.com/p/3fc194a1c56281a78de0d118bb2fb910
- **安全の決まり（実機に触る前に必ず読む）** — https://app.notion.com/p/3fc194a1c56281e69f78c10b93fadc2a
- This repo's daily checklist and spotter card: `docs/hardware-runbook.md` (go/no-go, rules R1–R4)

In brief (from the safety protocol):
| Level | How to stop | Effect / when |
|---|---|---|
| L0 prevent | speed / step limits, walls (this page), short episodes | limits how far a wrong motion can go |
| L1 normal stop | **End recording** on the page / Ctrl-C of the program | the program stops; teleop disables the motors on the way out |
| L1.5 soft e-stop | `touch /tmp/openarm_estop` (only programs that watch it, e.g. jev_arm.py; not the recorder) | hold → torque off → disconnect |
| L2 soft limp | `openarm-can-cli disable` on each CAN channel; check with `bash scripts/can_check.sh` (openarm-orboh) | torque off (may not work after an error latch) |
| L3 cut power | **the arm's DC power supply OFF → 10 s → ON** | the only sure way; the spotter keeps a hand near it |
- Before: nobody, no PC, cable or camera mount inside the follower's reach; the power switch within reach and **pointed at**
  (which switch = which arm); **never use `openarm-can-cli monitor`** (it switches torque ON) — use `show_param` or `can_check.sh`.
- During: strange noise, odd following or sudden vibration → End recording / Ctrl-C at once → L2 → L3 if needed. A CAN
  bus-off on one side → stop collecting (do not continue with one arm), tell Kento-san.
- After (every time, especially after a crash): confirm the motors are disabled; **after a crash treat the motors as still
  powered**; power-cycle anything that does not go limp; leave the arm hanging in a safe pose.
- This page is **not** an emergency stop: the spotter's e-stop / power switch is.

## Setup (once per PC)
```bash
cd ~/projects/PICO-controller
uv venv --python 3.10 .venv && uv pip install --python .venv/bin/python "pin==4.1.0" "numpy<2.3" pyzmq pyyaml pytest matplotlib "nicegui>=2,<3"
bash scripts/test.sh -q        # offline tests, no hardware
```
Also needed (as on the operator PC on 2026-11-01): the PICO setup (`docs/pico-working-setup.md`);
`~/projects/openarm-orboh` on `feat/takeei-wood-l1` @ e98e987 (recorder scripts, leader agents, pose sets, `scenes/`,
`canup.sh`); `~/projects/lerobot` on `feat/bi-openarm-soft-move-to` with its `.venv` (recorder, RealSense). Branches to be
confirmed by Kento-san. Safety: `docs/hardware-runbook.md` (go/no-go, R1-R4, spotter card) and the OpenArm safety protocol
(Notion; archived copy in `notion/`). The **spotter fills today's go/no-go column** in the runbook before the session.

## Start
```bash
bash ~/projects/PICO-controller/scripts/gui.sh     # open http://localhost:8090 on this PC (only this PC can open it)
```
English / 日本語: the button at the top right. Only one browser tab controls; other tabs watch ("Take control here").
**After every code update, restart the page** (Ctrl-C, then `gui.sh` again): the page runs the code it was started with.
Check in the recorder status line: **"keys from this page ✅"** (lerobot says "No display detected: reading recording
control keys from stdin"). If it says "keys NOT from this page", end the recording and restart the page.

## Connect the PICO (every day, before ①)
1. **Network — PC and headset on the same network**, one of:
   - Wi-Fi **`orboh`** (`nmcli dev wifi connect orboh` on the PC; on the headset: Settings → Wi-Fi → `orboh`), or
   - **Kota-san's mobile tethering (hotspot)**: PC and headset both join the phone's hotspot. Addresses are then not
     192.168.0.x: start the page with `PICO_HEADSET_IP=<headset ip> bash scripts/gui.sh` so the checks ping the right one.
   PC address: `ip -4 -br addr show wlp0s20f3` (the Wi-Fi interface; ignore docker / VPN addresses).
2. **PC Service** running on the PC (card 0 shows it; start command in "Working setup for PICO").
3. **In the headset, open the XRoboToolkit app** (Library → Unknown sources → XRoboToolkit) and check its panel, part by part:
   - **PC Service:** type the PC's address from step 1 → connect; wait until **Status: WORKING**.
   - **Data to send:** tick **Controller** (and **Headset**: the page uses it for the facing check).
   - Press **Send** so the app starts sending. **Do not press A afterwards** (A toggles sending off).
   - Close the PC Service's 3D demo window on the PC if it opened.
4. On the page press **Test PICO input**: `rate ~50–80 Hz | L ok | R ok | facing …`. 0 Hz → sending is off, the headset
   slept (keep it on), or the 3D demo is open.

## People
| Role | Does |
|---|---|
| Page operator (at the PC) | presses the buttons in order, writes the episode notes |
| Spotter (at the arm) | e-stop and power switch; types their name at ② and says "arm may move"; calls "wall, move back" |
| Operator (headset or leader arm) | teleoperates; releases the grip before the end |

## The page, top to bottom
- **Card 0 — Before you start: connections** (refreshed every 3 s, a toast when one is lost or back):
   PICO: PC on the headset's network (orboh or tethering), PC Service running, 3D demo closed, headset answers, PICO data (button "Test PICO input");
   leader: leader arm CAN up; both: follower CAN up, cameras (button), today's go/no-go all PASS, no other sender.
   Red items show what to do. Fix them before ①.
- **Card 1 — Setup**: method (PICO → follower / Leader arm → follower), arm (**left / right / both**), **stage**, episodes,
   seconds, operator name, task (same text for both methods), **scene** (gate/session, one arm: an openarm-orboh
   `scenes/<name>.yaml`); PICO: smooth motion (on), wrist rotation, pre-move.
   - **Both arms**: with the leader arms only. The page runs the team's `collect_bimanual.sh <dry|gate|session> [tag] [n]`
     exactly like the Collect tab (its task file `config/bimanual-collect.kitakyushu_takeei_sort.env`, its dataset names,
     its 4-CAN check); ① only prepares (no leader agent: the recorder reads both leader arms itself). A session needs a
     tag (e.g. `s2`). **Both arms with the PICO is not possible yet** (lerobot has no two-arm remote receiver).
   - **dry** = 2 throw-away episodes to check everything (dataset `…_tmp`);
   - **gate** = 10 episodes for review (dataset `Orboh/openarm_<method>_<arm>_gate` + the recorder's
     `_YYYYmmdd_HHMMSS`); **only a complete gate** (recorder exit 0 and all episodes saved) shows the **Gate review**
     card (reviewer's name + OK); a new gate cancels the previous OK;
   - **session** = the real collection (`…_session_<time>`), only after a reviewed gate for the same method, arm and task.
- **Card 2 — Run** ("Now: …" shows the next step; grey buttons cannot be pressed yet):
   ① Start teleop → ② spotter name (**must differ from the operator**) + "arm may move, power ON": the spotter switches
   the arm's power on now; if the follower CAN card is red afterwards, run `cd ~/projects/openarm-orboh && sudo bash
   scripts/canup.sh` → ③ Start recording (**refused while a CAN link is down; the arm moves** to the start pose) → ▶ Next episode / ↺ Discard + re-record / ⟲ Return to start (arm moves) / ■ End recording (**not an
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

## Did it save?
When the recorder ends, the status line shows **"saved N of M"** (from the run folder's `manifest.json`); a red banner
says **"NO EPISODE WAS SAVED"** otherwise. A dry run passes only with all episodes saved; then replay one episode
(e.g. open the dataset with lerobot's viewer) before a gate.

## When something goes wrong
| You see | Do |
|---|---|
| red banner "ANOTHER SENDER … E-STOP" | e-stop first, then find the other program |
| red banner "TELEOP / RECORDER EXITED" | spotter watches the arm; release; End recording; the episode is flagged |
| LOCKED | release → End recording → ④ → ① again |
| red banner "RECORDER FAILED: …" (e.g. CAN "Network is down") | the arm did not move; fix the cause (card 0), then **Abort recorder** (appears) → ④ |
| End recording pressed, recorder still running after 60 s | spotter turns the arm off or supports it (Ctrl-C makes a powered follower go limp) → **Abort recorder**; fallback in a terminal: `pkill -INT -f lerobot-record` |
| FAULT in live status | the arm **holds** (frozen target); release, wait for "ready", squeeze again |
| teleop stopped (④ / Ctrl-C of the PICO sender) | the receiver **holds** the last pose; End recording returns the arm to the start pose and it **goes limp** |
| a connection turns red | stop the episode (End recording), fix it (the card says how), start again from ① |
| closing the page / Ctrl-C of `gui.sh` | the page sends Esc to the recorder (may not end, see above) and stops a PICO sender; a **leader agent keeps running**: the leader operator holds the leader arm, then `pkill -INT -f run_leader_agent` |

## Where the data goes
Run folder `~/collections/<UTC>-<method>-<arm>-<stage>/` (console log, command, manifest), e.g.
`~/collections/20261101T115231Z-pilot-pico/` (older name); dataset `~/.cache/huggingface/lerobot/<repo id>_<date>_<time>`,
e.g. `~/.cache/huggingface/lerobot/Orboh/openarm_pilot_pico_tmp_20261101_195903` (dry names repeat with a new time
stamp, nothing is overwritten; local only, nothing is pushed); notes `runs/gui-…-notes.json`; gate reviews
`runs/gui-reviews.json` (**git-ignored: keep a copy**); PICO sender log `runs/gui-…-sender.jsonl`.
