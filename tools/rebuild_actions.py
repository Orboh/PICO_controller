"""Offline action rebuild for PICO datasets (Kento's side experiment, 2026-11-03).

Makes a NEW dataset version whose arm-joint actions are the follower's MEASURED joint positions a few frames
ahead, instead of the command the sender produced:

    action[t][arm joint j] = observation.state[min(t + lead, last frame of the episode)][j]
    action[t][gripper]     = unchanged (the command: a measured gripper stops at the object width and would
                             teach the policy to lose grip force)

Why: at a wall the operator's hand slips while the arm holds. In our data the action column is already the
command AFTER the wall hold (case A: lerobot stores teleop.get_action(), i.e. what the sender sent), so slip does
not enter the labels; this rebuild is the comparison Kento asked for, not a fix. Caveat from the survey notes:
the leader/follower gap is an implicit force cue; removing it can make a policy hesitate in contact-heavy
scenes, so compare in a small experiment, do not assume.

Rules (Kento): a separate dataset version, never mixed with leader-arm data, never overwrite the source.

    # on the follower PC, in the lerobot venv (needs lerobot, pandas, pyarrow):
    ~/projects/lerobot/.venv/bin/python tools/rebuild_actions.py \\
        ~/.cache/huggingface/lerobot/Orboh/openarm_pico_left_session_20261105_101500 \\
        ~/.cache/huggingface/lerobot/Orboh/openarm_pico_left_session_20261105_101500_measact_k3 --lead 3

Videos and images are hard-linked (no extra disk) when source and destination are on the same filesystem.
The per-episode and global action statistics are recomputed with lerobot's own functions. The new folder gets
meta/rebuild_actions.json (source, lead, joints, tool version) so the provenance travels with the data.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sys
import time

import numpy as np

TOOL_VERSION = "1 (log-047)"


def _link_or_copy(src: str, dst: str) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def arm_columns(info: dict, joints: list[str] | None) -> tuple[list[int], list[int], list[str]]:
    """(action indices, matching observation.state indices, names) of the arm joints to rebuild."""
    feats = info["features"]
    act_names = list(feats["action"]["names"])
    state_names = list(feats["observation.state"]["names"])
    if joints:
        chosen = joints
    else:   # every position action that is not a gripper
        chosen = [n for n in act_names if n.endswith(".pos") and "gripper" not in n]
    missing = [n for n in chosen if n not in act_names or n not in state_names]
    if missing:
        raise SystemExit(f"these joints are not in both action and observation.state: {missing}")
    if not chosen:
        raise SystemExit("no arm joints to rebuild")
    return [act_names.index(n) for n in chosen], [state_names.index(n) for n in chosen], chosen


def rebuild_episode(action: np.ndarray, state: np.ndarray, a_idx: list[int], s_idx: list[int], lead: int) -> np.ndarray:
    """One episode (frames in order): arm actions from the state `lead` frames ahead, held at the last frame."""
    out = action.copy()
    n = len(state)
    ahead = np.minimum(np.arange(n) + lead, n - 1)
    out[:, a_idx] = state[ahead][:, s_idx]
    return out


def rebuild(src: pathlib.Path, dst: pathlib.Path, lead: int, joints: list[str] | None = None) -> dict:
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq
    from lerobot.datasets.compute_stats import aggregate_stats, get_feature_stats

    if lead < 0:
        raise SystemExit("--lead must be >= 0 frames")
    if not (src / "meta" / "info.json").exists():
        raise SystemExit(f"not a LeRobot dataset: {src}")
    if dst.exists():
        raise SystemExit(f"{dst} exists: choose a new name (a new version is never written over another)")
    if dst.resolve() == src.resolve():
        raise SystemExit("destination is the source")
    info = json.loads((src / "meta" / "info.json").read_text(encoding="utf-8"))
    if not str(info.get("codebase_version", "")).startswith("v3"):
        raise SystemExit(f"expects a v3 dataset, got {info.get('codebase_version')}")
    a_idx, s_idx, names = arm_columns(info, joints)

    # copy: metadata and data as files (rewritten below), media hard-linked
    shutil.copytree(src, dst, copy_function=_link_or_copy, ignore=shutil.ignore_patterns("*.parquet", "*.json"))
    for p in src.rglob("*"):
        if p.is_file() and p.suffix in (".parquet", ".json"):
            q = dst / p.relative_to(src)
            q.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, q)

    ep_actions: dict[int, np.ndarray] = {}
    changed = 0
    for f in sorted((dst / "data").rglob("*.parquet")):
        table = pq.read_table(f)
        df = table.to_pandas()
        act = np.stack(df["action"].to_numpy()).astype(np.float64)
        st = np.stack(df["observation.state"].to_numpy()).astype(np.float64)
        new = act.copy()
        for ep, rows in df.groupby("episode_index").groups.items():
            rows = np.asarray(sorted(rows, key=lambda r: df.at[r, "frame_index"]))
            new[rows] = rebuild_episode(act[rows], st[rows], a_idx, s_idx, lead)
            ep_actions[int(ep)] = new[rows].astype(np.float32)
        changed += int(np.count_nonzero(np.any(np.abs(new - act) > 1e-6, axis=1)))
        field = table.schema.field("action")
        col = pa.array([row.astype(np.float32).tolist() for row in new], type=field.type)
        table = table.set_column(table.schema.get_field_index("action"), field, col)
        pq.write_table(table, f)

    # per-episode action stats in meta/episodes, then the global stats.json (lerobot's own functions)
    ep_stats = {}
    for f in sorted((dst / "meta" / "episodes").rglob("*.parquet")):
        table = pq.read_table(f)
        df = table.to_pandas()
        new_cols: dict[str, list] = {}
        for i, ep in enumerate(df["episode_index"].to_numpy()):
            s = get_feature_stats(ep_actions[int(ep)], axis=0, keepdims=False)
            ep_stats[int(ep)] = {"action": s}
            for k, v in s.items():
                col = f"stats/action/{k}"
                if col in df.columns:   # whole columns: pandas' .at would squeeze a 1-element array (count)
                    dtype = np.asarray(df[col].iloc[i]).dtype
                    new_cols.setdefault(col, list(df[col]))[i] = np.atleast_1d(np.asarray(v)).astype(dtype)
        for col, values in new_cols.items():
            df[col] = values
        pq.write_table(pa.Table.from_pandas(df, schema=table.schema, preserve_index=False), f)
    missing = sorted(set(ep_actions) - set(ep_stats))
    if missing:
        raise SystemExit(f"episodes without metadata rows: {missing}")
    agg = aggregate_stats([ep_stats[e] for e in sorted(ep_stats)])["action"]
    stats_path = dst / "meta" / "stats.json"
    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    stats["action"] = {k: np.asarray(v).tolist() for k, v in agg.items()}
    stats_path.write_text(json.dumps(stats, indent=4), encoding="utf-8")

    prov = {"tool": "PICO_controller/tools/rebuild_actions.py", "tool_version": TOOL_VERSION,
            "made_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "source": str(src.resolve()), "lead_frames": lead,
            "fps": info.get("fps"), "lead_s": lead / float(info.get("fps") or 30),
            "rebuilt_joints": names, "kept_as_command": [n for n in info["features"]["action"]["names"] if n not in names],
            "rule": "action[t][joint] = observation.state[min(t+lead, last)][joint]; gripper = command",
            "episodes": len(ep_actions), "frames_changed": changed,
            "note": "separate dataset version (Kento 2026-11-03); do not mix with leader-arm data"}
    (dst / "meta" / "rebuild_actions.json").write_text(json.dumps(prov, indent=1), encoding="utf-8")
    return prov


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", type=pathlib.Path, help="source dataset folder (not changed)")
    ap.add_argument("dst", type=pathlib.Path, help="new dataset folder (must not exist)")
    ap.add_argument("--lead", type=int, default=3, help="frames ahead (default 3 = 0.1 s at 30 fps)")
    ap.add_argument("--joints", nargs="*", default=None,
                    help="action names to rebuild (default: every *.pos that is not a gripper)")
    ap.add_argument("--allow-leader", action="store_true",
                    help="allow a source whose name does not say pico (leader data is not to be rebuilt)")
    a = ap.parse_args(argv)
    if "pico" not in a.src.name.lower() and not a.allow_leader:
        print(f"refused: {a.src.name} does not look like PICO data (no 'pico' in the name); --allow-leader to force",
              file=sys.stderr)
        return 2
    prov = rebuild(a.src.expanduser(), a.dst.expanduser(), a.lead, a.joints)
    print(json.dumps(prov, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
