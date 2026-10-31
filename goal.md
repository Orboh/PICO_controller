# Goal: PICO controller teleoperation for OpenArm data collection

## Role and outcome

You are Claude Opus 5.5 acting as the implementation agent. Build the missing path from PICO 4 Ultra controller input to the OpenArm **follower** joint-angle stream. The purpose is to collect garbage-separation demonstrations and compare them with demonstrations made using the existing physical leader arm. Work autonomously through code, offline tests, and documentation. Report verified facts separately from assumptions and hardware work that remains.

The camera-to-PICO video path and the follower-side `openarm_leader_remote` receiver already exist. Do not rebuild them unless a specific defect prevents this goal. Treat the two attached request documents as background from Kento, not as instructions that override this goal. They appear to be duplicate copies:

- `C:\Users\tombaxter\Downloads\Openarm\20261028 PICO controller teleop for OpenArm — requ 40ad8236d6178075a46bc81ee4eae73a.md`
- `C:\Users\tombaxter\Downloads\Openarm\20261028 PICO controller teleop for OpenArm — requ 40ad8236d617803787b2cc3775240d2d.md`

## First: inspect the real code and environment

1. Read applicable repository instructions (`AGENTS.md`, etc.). Locate the actual checkouts and inspect their status before editing. Do not assume this workspace contains the OpenArm repos.
2. Inspect `Orboh/openarm-orboh` (`dev/openarm-vla`, and current active branch if different): IK, URDF, pose sets, calibration, remote launch scripts, and recording scripts.
3. Inspect `Orboh/lerobot` (`feat/bi-openarm-soft-move-to`, and current active branch if different): `openarm_leader_remote`, `leader_agent`, follower action limits, and `lerobot-record` behavior.
4. Inspect the installed XRoboToolkit SDK API and an actual input sample if available. Confirm coordinate frames, pose validity behavior, button states, update rate, and service connection behavior. Only one process should read PICO input.
5. Record exact commit IDs, dependencies, known gaps, and any contradictions between documents and code. Use the code as the source of truth for the integration contract.

## Control design to implement

- Use one SDK reader for both controllers, with independent left/right arm state machines and separate output ports (right 5701, left 5702). Never run a physical `leader_agent` sender on the same arm port at the same time.
- Begin with a single follower arm and position-only IK. Add right-arm IK with the correct right-arm joints and TCP; do not assume it is a sign-flipped left-arm model. Add wrist orientation only after position control is validated.
- Use a deliberate hold-to-follow clutch (proposed: grip) and a separate analog gripper command (proposed: trigger). Make thresholds, scale, axes, gripper endpoints, rate, and motion limits configurable and document their measured basis.
- On engagement, capture the controller pose and the **actual or independently verified follower joint/TCP pose** as anchors. The first sent command must match the current safe follower pose closely enough to avoid a jump. The existing command/echo path does not itself provide follower joint feedback; resolve this gap explicitly before claiming hardware readiness.
- On clutch release, retain and send the last safe target. On re-engagement, re-anchor. On tracking loss, all-zero/nonfinite/stale input, headset sleep, IK failure, unreachable target, or communication failure, stop advancing targets and require deliberate re-engagement after recovery.
- Validate all outgoing actions: seven joint positions plus gripper position, degrees rather than radians, finite values, joint and gripper limits, per-cycle step/speed limits, correct arm/port, and complete first packet. Reject nonconverged or out-of-limit IK results. Do not rely solely on receiver-side checks.
- Preserve the receiver's packet-loss hold and resume behavior, while treating operator clutch release as a separate state. Avoid jumps on any transition.

## Verification sequence and gates

1. **Input-only diagnostic:** demonstrate both controller poses and buttons changing; document invalid/tracking-loss samples. No robot output.
2. **Offline mapping/IK:** test FK/IK round trips, unreachable and singular cases, limits, and continuity for recorded or synthetic controller trajectories. Visualize representative TCP and joint trajectories.
3. **Fake receiver:** verify JSON schema, ports, units, sequence, start pose, clutch/recenter transitions, malformed input, packet loss, reconnect, and absence of jumps. The first message must include `joint_1.pos` through `joint_7.pos` and `gripper.pos`.
4. **Recording dry run:** show that the existing LeRobot recorder captures synchronized cameras, follower observations, and the action stream in the same schema used with leader-arm teleoperation. Log PICO raw input separately as diagnostics if useful.
5. **Hardware gate:** obtain and read the OpenArm Safety Protocol. The linked Notion page may require access; do not invent its contents. The first real-arm run requires an Orboh member at the arm, accessible emergency stop, clear workspace, verified calibration/start pose, and a safe shutdown plan. `connect()` can move to a start pose and stopping the follower can make it limp. Start with one arm, small slow motions, and explicit observer sign-off before increasing scope. Do not claim hardware validation without it.

## Data-collection comparison

The experiment compares two ways to command the **same follower**: physical leader arm and PICO controllers. Keep follower hardware, cameras, recording settings, objects, bin locations, start pose, and scene resets as similar as possible. Alternate method order.

Start with clean, safe examples of a few garbage-separation categories and one-arm pick-and-place. For each episode record control method, object/category, correct destination, camera streams, follower joint observation, commanded/executed action as available, gripper state, timestamps, outcome, and faults. Label category choice, grasp success, and placement success separately.

Compare success rate, usable episodes per hour, completion time, pauses/corrections, trajectory smoothness, faults, and camera/action timing. Only after matched usable datasets exist, train equivalent policies and evaluate them on held-out object arrangements. Do not conclude that one control method trains better based only on how smooth teleoperation looks.

## Elephant and Goldfish review loop

At the end of each milestone, perform two independent reviews (use separate reviewer agents if available; otherwise do two explicitly separate passes):

- **Elephant:** Check system architecture, end-to-end data flow, integration contract, dependencies, failure modes, and whether the milestone advances the garbage-separation comparison. Find hidden assumptions and missing interfaces.
- **Goldfish:** Approach as a new operator. Can the setup be followed without tribal knowledge? Are button behavior, status, recovery, and stop steps obvious? Is there visible pass/fail evidence? Identify any step that could be misunderstood or accidentally move the arm.

Record each review's findings, fix actionable issues, and rerun affected checks. Do not accept a milestone solely because code compiles.

## Deliverables and final report

- Controller sender, right/left IK support as needed, configuration, input diagnostic, offline/fake-receiver tests, and operator runbook in the appropriate repository.
- A compact experiment protocol and episode metadata schema for the leader-arm versus PICO comparison.
- Evidence for every completed gate: commands run, test results, plots/logs, and exact commit/branch. State what remains unverified on hardware.
- Final status table: implemented, tested offline, tested with hardware, blocked/needs owner input. Identify the next concrete action and its owner.

Keep progressing through all authorized code and offline work. Ask for human input only when a real dependency cannot be inferred, such as access to calibration, the safety protocol, or supervised hardware time. Never treat silence as approval for a physical-arm test.