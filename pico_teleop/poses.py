"""Start poses from openarm-orboh pose sets (the follower aligns to the same file on connect)."""
from __future__ import annotations

import os

import yaml

POSE_SETS = os.path.expanduser("~/projects/openarm-orboh/config/pose_sets")
JOINTS8 = [f"joint_{i}" for i in range(1, 8)] + ["gripper"]


def current_pose_set(pose_sets_dir: str = POSE_SETS) -> str:
    """Name in <pose_sets_dir>/CURRENT (the set the follower scripts use when POSE_SET is unset)."""
    with open(os.path.join(pose_sets_dir, "CURRENT")) as f:
        return f.read().strip()


def load_start_pose(side: str, pose_set: str | None = None, pose_sets_dir: str = POSE_SETS) -> dict[str, float]:
    """<set>/<side>.yaml top-level joint_1..7 + gripper (lerobot degrees) -> action dict."""
    path = os.path.join(pose_sets_dir, pose_set or current_pose_set(pose_sets_dir), f"{side}.yaml")
    with open(path) as f:
        doc = yaml.safe_load(f) or {}
    missing = [k for k in JOINTS8 if k not in doc]
    if missing:
        raise ValueError(f"{path}: missing {missing}")
    return {f"{k}.pos": float(doc[k]) for k in JOINTS8}
