## 7. Update (evening): wall fix finished offline, and the jerk analysis

### 7.1 Wall fix: final offline result (log-030)
- Two more edge cases became walls: a big joint jump near a stretched arm ("IK jumped", the jump is never sent), and going deeper below the hard stretched-arm floor. Tests: 116 passed.
- Replays of every locked session with the final code (same recorded hand motion, no robot):

| Session | Real result | Final code |
| --- | --- | --- |
| Pilot block 11-01 (928 s) | LOCKED | 0 FAULTs, no lock, 24 s at a wall |
| Pilot redo 10-31 | LOCKED | 0 FAULTs, no lock |
| Left wrist W1 / right wrist R2 | LOCKED | 0 FAULTs, no lock |
| Session 4 (10-31, 58 FAULTs) | many FAULTs | 3 real tracking FAULTs, no lock, no creep (≥ 3° from every limit) |

- **Status: offline ✅, robot ❌ (next: supervised wall check on one arm).**

### 7.2 Why PICO episodes are jerkier than leader episodes (log-032)
- Not mainly the speed limit. Main cause: **rate mismatch**: the PICO sender sends 50 targets per second, the recorder takes 30 per second → small/big/small steps (61–63 % of frames vs 16–17 % for the leader), a ~10 Hz wobble. Smaller causes: the hard cut at the speed limit, and short PICO input gaps.
- Two fixes, both optional (defaults unchanged): sender at **60 Hz** (2 steps per frame) + **ease in/out** (acceleration limit 300°/s²).
- Offline test bench (real PICO input, recorder at 30 fps):

| Setting | Jerk ep 0 | Jerk ep 1 | Path change |
| --- | --- | --- | --- |
| today (50 Hz) | 40 k | 66 k | — |
| **60 Hz + 300°/s²** | **22.5 k (−44 %)** | **22 k (−66 %)** | ~1° (95 %) |
| leader episodes (for comparison) | 27–39 k | | |

- **Status: offline ✅, robot ❌.** To try on the arm: `HZ=60 ACC=300 bash scripts/gate4_sender.sh <tag>`.

### 7.3 Online tests still to do (this page will be updated after them)
- [ ] Wall check on the robot (one arm, separate spotter)
- [ ] 60 Hz + ease in/out on the robot, then a PICO block × 5; compare jerk with the leader
