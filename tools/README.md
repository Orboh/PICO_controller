# Offline analysis tools (log-027 … log-035)
Run with `bash scripts/pico_env.sh python tools/<name>.py …` from the repo root.
Replay input = the `frame` lines of a sender log: `python3 -c` one-liner in log-035, or `tools/frames.sh <run>`.
| Tool | What |
|---|---|
| `fastreplay.py <frames> <side> <0/1 wrist> [hz] [acc]` | whole run through the controller (no real time): FAULT / LOCK / wall counts, min room |
| `jerkbench.py <frames> <side> <t0> <t1> <hz:acc,…>` | smoothness when sampled like lerobot-record (30 fps) |
| `wristcheck.py <sender.jsonl> <side>` | per squeeze: hand turn vs commanded gripper turn |
| `rotspeed.py <sender.jsonl>` | hand rotation speed around WALL / FAULT events |
| `wristsim2.py <side>` / `wristsim3.py` | how far each wrist direction can turn (from start / after moving forward) |
| `wall_report.py [notes…] [--from-log] [--json]` | log-047: per-episode wall time + hand slip of PICO episodes from the page's notes, distribution, flagged list (> 3 s / > 20 cm, provisional) |
| `rebuild_actions.py <src> <dst> --lead 3` | log-047 (Kento's side experiment): NEW dataset version whose arm actions = follower's measured joints `lead` frames ahead, gripper kept; run in the lerobot venv |
