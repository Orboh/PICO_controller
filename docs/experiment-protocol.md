# Leader arm vs PICO controllers — data-collection comparison protocol (draft)

Purpose: compare two ways of commanding the **same follower** for garbage-separation demonstrations,
then (only once matched usable datasets exist) train equivalent policies and compare them on held-out
arrangements. Smooth-looking teleop is not evidence that a method trains better.

## Held constant
Follower arm(s) and calibration file · cameras, positions and `start-collection-cameras.sh` split ·
`record_pick_place_openarm.sh` with `REMOTE=true` (both methods go through `openarm_leader_remote`,
so the action schema is identical) · fps, episode/reset time · pose set (`config/pose_sets/CURRENT`)
· objects, bin positions (marked with tape), scene reset photo · operator (or a balanced operator mix).

| Method | Sender on the operator PC | Teleop id on the follower |
|---|---|---|
| `leader` | `run_leader_agent_<side>.sh` (`leader_agent`, 200 Hz) | `leader_<side>_remote` |
| `pico` | `pico_teleop.sender` (50 Hz, this repo) | `leader_<side>_remote` |

Never run both senders for the same arm at the same time (same port).

## Session plan
1. Gates 1-3 passed for the build in use; gate 5 sign-off for PICO on the real arm.
2. One arm, one-arm pick-and-place first; then 3-4 garbage categories (e.g. PET bottle, can, paper,
   wood piece), each with a correct bin.
3. Blocks of 5 episodes, **alternating method order** per block (ABBA: leader, pico, pico, leader).
4. Same object list per block for both methods (shuffled order, seed written in the metadata).
5. Reset the scene to the photo between episodes, arm in HOLD / at the start pose (record 'a' key).

## Per-episode metadata (one JSON line per episode, `episodes.jsonl` next to the dataset)
| Field | Type | Meaning |
|---|---|---|
| `dataset_repo_id`, `episode_index` | str, int | links to the LeRobot episode |
| `control_method` | `"leader"` / `"pico"` | the sender used |
| `sender_commit` | str | `git rev-parse --short HEAD` of PICO-controller or lerobot |
| `sender_log` | str | `runs/sender-*.jsonl` (pico raw input + states) or leader_agent log |
| `operator`, `arm`, `pose_set` | str | |
| `block`, `order_in_block`, `shuffle_seed` | int | ABBA bookkeeping |
| `object_id`, `category`, `correct_bin` | str | |
| `chosen_bin` | str or null | where it was put |
| `category_correct` | bool | `chosen_bin == correct_bin` (labelled separately from motion success) |
| `grasp_success`, `place_success` | bool | grasp held to the bin; object ended inside the bin |
| `t_start`, `t_grasp`, `t_place`, `t_end` | float s | from the recording |
| `pauses`, `corrections` | int | HOLD periods > 1 s while ENGAGED-intended; re-grasps |
| `faults` | list[str] | PICO FAULT events / receiver holds / CAN errors |
| `usable` | bool | reviewer decision after watching the video |
| `notes` | str | |

## Measures
Success rate (category, grasp, place, all three) · usable episodes per hour (wall clock incl. resets)
· completion time · pauses / corrections · joint-path smoothness (sum of squared jerk of the recorded
action) · faults per hour · camera vs action timing (frame timestamps vs action timestamps; PICO
packets carry `t_mono` on the operator PC only: record the PC-to-PC offset from the RTT echo).

## Training comparison (later)
Only after both methods have the same number of usable episodes per category: train the same policy
(same architecture, steps, seed set) on each, evaluate on held-out object arrangements with the
same rollout protocol, report success with confidence intervals.
