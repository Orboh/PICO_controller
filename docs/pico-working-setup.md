# Working setup for the PICO (how the PICO is connected to the PC and the arm)

Status 2026-11-01: working on this PC; PICO → **follower** arm (left and right) tested on the robot.
Planned: PICO → **leader** arm (the PICO moves the leader arm), not built yet.

## Hardware and network
| Part | What we use |
|---|---|
| Headset | PICO 4 Ultra, two controllers; developer mode on, Safeguard off |
| App on the headset | XRoboToolkit-PICO (APK from github.com/XR-Robotics → XRoboToolkit-Unity-Client releases, installed with File Manager → APKs) |
| Network | PC and headset on the **same network**: Wi-Fi **`orboh`**, or **Kota-san's mobile tethering (hotspot)** (no internet needed). On 11-01: PC **192.168.0.136**, headset **192.168.0.233** (DHCP: check each day; the website pings `PICO_HEADSET_IP`, default .233: `PICO_HEADSET_IP=<ip> bash scripts/gui.sh`) |
| PC | Ubuntu, this PC; PICO PC Service in `/opt/apps/roboticsservice` |
| Python binding | `~/Desktop/XRoboToolkit-PC-Service-Pybind` (commit 75cb113, py3.10 build), used by `scripts/pico_env.sh` |
| Arm side | lerobot receiver `openarm_leader_remote` (openarm-orboh `run_teleop_remote_<side>.sh` or the recorder) |

## Data path
```
PICO controllers ─Wi-Fi orboh─▶ PC Service (TCP :63901) ─▶ xrobotoolkit_sdk (127.0.0.1:60061)
  ─▶ pico_teleop sender (ONE reader only; lock /tmp/pico-controller-xr-reader.lock)
  ─▶ ZMQ joint targets :5701 (right) / :5702 (left), echoes :5711 / :5712 ─▶ openarm_leader_remote ─▶ follower arm
```
The sender maps the controller's movement since the squeeze to the gripper (half the distance; wrist turn 1:1 in
wrist mode), solves the arm's joints (IK), checks every safety limit (walls, FAULTs) and sends 50-60 targets/s.

## Start, every day
1. **PC Service**: `ss -tln | grep -E '63901|60061'` shows both ports → running. Otherwise:
   `cd /opt/apps/roboticsservice && LD_LIBRARY_PATH=/opt/apps/roboticsservice:/opt/apps/roboticsservice/lib:/opt/apps/roboticsservice/SDK/x64 nohup ./RoboticsServiceProcess > /tmp/roboticsservice.log 2>&1 &`
2. **Close the 3D demo window** (`pgrep -af RobotLinuxDemo` must be empty): it is a second reader and steals the input.
3. **Network**: PC on `orboh` (`nmcli dev wifi connect orboh`) or on Kota-san's tethering, the same network as the headset; use the `192.168.0.x` address of the **Wi-Fi interface**
   (`ip -4 -br addr show wlp0s20f3`); other addresses in `hostname -I` are docker / VPN / USB.
4. **App (in the headset)**: open **XRoboToolkit** (Library → Unknown sources) and go through its panel:
   - **PC Service:** type the PC's address (step 3) → connect → wait for **Status: WORKING**;
   - tick **Controller** (and **Headset**, used for the facing check);
   - press **Send** to start sending; from then on **never press A** (it toggles sending off).
   With tethering: both devices on the phone's hotspot, use the PC's hotspot address here, and start the website
   with `PICO_HEADSET_IP=<headset ip>`.
5. **Check**: `bash ~/projects/PICO-controller/scripts/check_input.sh` → `rate ~50-80 Hz | L ok | R ok | facing …`
   — or the collection website's "Test PICO input" button. Its automatic card checks the network, service, demo window,
   headset ping and CAN links, but **not** the app's Status WORKING / sending: only the input test shows that.

## How to operate the PICO controllers (operator in the headset)
**Stand and face:** stand behind the robot on the tape mark, **facing the same way the robot faces**, then recenter the
view (long press of Home). Your forward = the arm's forward. A squeeze is refused if you face more than 75° away;
once moving you may turn your head to watch the arm. **Left controller = left arm, right controller = right arm.**

**Move the arm — the GRIP button** (the side button under the middle finger):
1. **Squeeze the grip and keep holding it.** After **0.3 s** the arm is connected (`ENGAGED`). It does **not** jump to
   your hand: from now on it copies how your hand **moves**, at **half the distance** (hand 10 cm → gripper 5 cm),
   up to 0.25 m/s.
2. **Move your hand** forward / back, left / right, up / down: the gripper moves the same way (robot's directions).
3. **Release the grip fully** → the arm **stops and holds** where it is (`HOLD`); your hand can move freely.
4. **To reach further, "clutch":** release, bring your hand back to a comfortable place, squeeze again, continue.
   Every new squeeze starts from where the arm is now.
- Half-squeezed (0.4–0.6) changes nothing. A short accidental squeeze (< 0.3 s) does nothing.

**Open and close the gripper — the TRIGGER** (index finger), **only while the grip is held:**
- Trigger **released** = gripper **open** (−58°); trigger **fully pulled** = gripper **closed** (0°); in between = in
  between. To grasp: hold the grip, move the open gripper around the object, then **pull the trigger** slowly.
  To place: keep the grip, move over the target, **release the trigger** → it opens.
- **Gentle sync:** right after each squeeze the gripper moves **slowly (30°/s)** to where your trigger is; once it
  matches it follows quickly (up to 120°/s). So keep the trigger where the gripper already is when you squeeze
  (e.g. keep it pulled while carrying an object), or the gripper slowly opens / closes to match.
- With the grip released (HOLD) the gripper does not follow the trigger.

**Turn the wrist** (only when wrist rotation is switched on): while squeezing, turning your hand turns the gripper
by the **same angle** (roll, pitch, yaw), at most 90°/s; the angle you held at the squeeze counts as zero.

**Other buttons**
| Button | Effect |
|---|---|
| **A** (right) | toggles sending in the app → **never press during a session** (the arm FAULTs) |
| **B** (right) | only the app's video view (2D / 3D), when video is used |
| **Y** (left) | PICO video on/off (hold 1 s), only if the page was started with video; never while the arm follows |
| X, menu, sticks | nothing |
| **Home** | long press = recenter (OK); short press leaves the app (don't) |

**What you may see**
| Message | Meaning | Do |
|---|---|---|
| `WALL: …` (the arm stops) | the edge of the safe range (joint limit, arm stretched, too far from the start, turned too far / too fast) | move back; the arm follows again at once |
| `FAULT` | something is wrong (tracking lost, hand jumped, link) — the arm **freezes** | release the grip, wait for the spotter's "ready", squeeze again |
| `LOCKED` | repeated FAULTs | stop: release, the page operator ends the recording and restarts |
| facing refused | you face > 75° away | turn your body to the robot's forward |
Keep both controllers in the headset's view (a hidden controller can jump when seen again → FAULT).

## Rules learned (what went wrong before)
| Pitfall | What to do |
|---|---|
| **A** button turns sending off (arm FAULTs) | never press A during a session (button table above) |
| Short press of Home leaves the app | only a long press (recenter), before squeezing |
| Headset sleeps ~8 s after it is taken off | keep it on; wake it, check the input again |
| 3D demo window open | close it (second reader) |
| Input gaps of 0.2-0.35 s about every 15 s (headset/app side) | the sender tolerates up to 0.4 s; longer → FAULT, release and squeeze again |
| Operator not facing the robot's forward | the squeeze is refused above 75°; turn the body / recenter |
| Only one PICO reader | stop check_input / dry runs before starting the sender |
| Still 0 Hz after all of the above | `pkill -f RoboticsServiceProcess`, start it again (step 1), reconnect the app |

## Safety (summary; full rules: Notion "OpenArm 安全プロトコル" https://app.notion.com/p/3fc194a1c56281a78de0d118bb2fb910, docs/hardware-runbook.md)
Separate spotter at the e-stop and the power switch; today's go/no-go column all PASS; the arm follows only while the
grip is squeezed; at the workspace edge the arm holds (WALL) instead of locking.
