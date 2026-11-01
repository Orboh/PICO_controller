## 10. Review of the day (Elephant + Goldfish) and fixes (log-035)

**Corrections to this page**
- The robot wall check showed **joint-limit, drift and wrist-turning walls**. The **stretched-arm, out-of-reach and joint-jump walls have not happened on the robot yet** (offline only).
- The limit numbers in §9 are the values we **sent** to the arm; there is no feedback from the arm yet. That the arm held at each wall was judged by the spotter.
- The ease-in/out setting can leave a joint up to 0.5° inside the 3° guard (never closer than 2.5° in the test), not "no overshoot".

**Code fixes (tests: 120 passed; replays: still no lock in any session)**

| Problem found | Fix |
| --- | --- |
| An arm already very stretched could not move out (every move FAULTed → lock) | only a move that makes it straighter FAULTs; moving out is allowed |
| The per-cycle "wall" mark in the log was too wide (12 072 cycles vs 4 620 real) | now marks only cycles where the arm was actually held; cleared on release / FAULT |
| A squeeze or lost connection did not cancel the pre-move | both cancel it now; pre-move only for one arm |
| Stopping the video could pause the control loop up to 6 s | never toggled while following; stops without waiting |

Also: the offline tools are now saved in the repo (`tools/`), and the README and the safety runbook are updated (the spotter calls "wall, move back", because the operator cannot see the wall message in the headset).

**Process points (to fix before collecting data)**
- Code went to the robot within minutes of each commit, before this review. New rule: offline replays + tests (+ wrist dry-run check for rotation changes) **before** every robot session.
- Today's approvals were in person only (joint_1 120°, right arm, the wall behaviour) → get a written OK in Slack.
- Before PICO data collection: wall check on the right arm; supervised check of 60 Hz + ease in/out; a rule for episodes recorded against a wall (time at a wall / hand slip); Kento-san's joint_7 offset for the leader comparison.
