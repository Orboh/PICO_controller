# Working setup for the PICO (how the PICO is connected to the PC and the arm)

Status 2026-11-01: working on this PC; PICO → **follower** arm (left and right) tested on the robot.
Planned: PICO → **leader** arm (the PICO moves the leader arm), not built yet.

## Hardware and network
| Part | What we use |
|---|---|
| Headset | PICO 4 Ultra, two controllers; developer mode on, Safeguard off |
| App on the headset | XRoboToolkit-PICO (APK from github.com/XR-Robotics → XRoboToolkit-Unity-Client releases, installed with File Manager → APKs) |
| Network | Wi-Fi **`orboh`** for both PC and headset (no internet needed). On 11-01: PC **192.168.0.136**, headset **192.168.0.233** (DHCP: check each day; the website pings `PICO_HEADSET_IP`, default .233: `PICO_HEADSET_IP=<ip> bash scripts/gui.sh`) |
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
3. **Wi-Fi**: PC on `orboh` (`nmcli dev wifi connect orboh`); use the `192.168.0.x` address of the **Wi-Fi interface**
   (`ip -4 -br addr show wlp0s20f3`); other addresses in `hostname -I` are docker / VPN / USB.
4. **App**: "PC Service:" = that address → wait for **Status: WORKING** → tick **Controller** → sending **ON**.
5. **Check**: `bash ~/projects/PICO-controller/scripts/check_input.sh` → `rate ~50-80 Hz | L ok | R ok | facing …`
   — or the collection website's "Test PICO input" button. Its automatic card checks the network, service, demo window,
   headset ping and CAN links, but **not** the app's Status WORKING / sending: only the input test shows that.

## Rules learned (what went wrong before)
| Pitfall | What to do |
|---|---|
| **A** button turns sending off (arm FAULTs) | never press A during a session; B switches the video view |
| Short press of Home leaves the app | only a long press (recenter) |
| Headset sleeps ~8 s after it is taken off | keep it on; wake it, check the input again |
| 3D demo window open | close it (second reader) |
| Input gaps of 0.2-0.35 s about every 15 s (headset/app side) | the sender tolerates up to 0.4 s; longer → FAULT, release and squeeze again |
| Operator not facing the robot's forward | the squeeze is refused above 75°; turn the body / recenter |
| Only one PICO reader | stop check_input / dry runs before starting the sender |
| Still 0 Hz after all of the above | `pkill -f RoboticsServiceProcess`, start it again (step 1), reconnect the app |

## Safety (summary; full rules in docs/hardware-runbook.md)
Separate spotter at the e-stop and the power switch; today's go/no-go column all PASS; the arm follows only while the
grip is squeezed; at the workspace edge the arm holds (WALL) instead of locking.
