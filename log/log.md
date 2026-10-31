# Work log index — PICO-controller

Rules for this folder:
- One topic per file: `log-001.md`, `log-002.md`, …, in the order the work happened.
- Each log says **which option we chose, why, and what each function does and where** (`file:line`).
- No file may be longer than **180 lines**, this index included. A topic that grows past 180 lines continues in the next numbered file.
- Add one row here for every new log.

| File | Why this log exists |
|------|---------------------|
| [log-001.md](log-001.md) | Created the `PICO-controller` project folder and this log folder: the folder name and the log rules. |
| [log-002.md](log-002.md) | Read-only inspection: commits, the receiver/follower contract, the SDK's stale-value behaviour, NaN-to-upper-limit and no-follower-feedback findings, and why the code lives here. |
| [log-003.md](log-003.md) | Gate 1: guided input-only diagnostic (`xr_input.py`, `diag_input.py`), 12 offline tests, Elephant/Goldfish findings and fixes; live PASS pending the headset. |
| [log-004.md](log-004.md) | Gate 2 offline: frames, per-side IK, packet validator, HOLD/ENGAGED/FAULT clutch, gripper soft takeover; 43 tests, trajectory plots; bugs found in the plots (false jump, IK staircase) fixed; right-arm posture-swing finding. |
