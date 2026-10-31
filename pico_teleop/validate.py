"""Outgoing action validation, run on EVERY packet before it leaves the sender.

Why sender-side checks are needed (receiver/follower code read 2026-10-31, log-002):
- `openarm_leader_remote._apply_packet` does `float(value)` with no finiteness check.
- `OpenArmFollower.send_action` clips with `max(lo, min(hi, x))`; with x = NaN Python's min/max
  return `hi`, so a NaN joint would be driven to its UPPER limit instead of being rejected.
- `max_relative_target` is None by default, so the follower has no step limit of its own.

Generic over the action source (PICO now, leader arm later).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

ACTION_KEYS = [f"joint_{i}.pos" for i in range(1, 8)] + ["gripper.pos"]

# lerobot/src/lerobot/robots/openarm_follower/config_openarm_follower.py (feat/bi-openarm-soft-move-to
# a720ec19). Equal to the URDF limits; the right arm is NOT the left one with joint_5..7 flipped.
FOLLOWER_LIMITS_DEG: dict[str, dict[str, tuple[float, float]]] = {
    "left": {"joint_1": (-200.0, 80.0), "joint_2": (-190.0, 10.0), "joint_3": (-90.0, 90.0),
             "joint_4": (0.0, 140.0), "joint_5": (-90.0, 90.0), "joint_6": (-45.0, 45.0),
             "joint_7": (-90.0, 90.0), "gripper": (-65.0, 0.0)},
    "right": {"joint_1": (-80.0, 200.0), "joint_2": (-10.0, 190.0), "joint_3": (-90.0, 90.0),
              "joint_4": (0.0, 140.0), "joint_5": (-90.0, 90.0), "joint_6": (-45.0, 45.0),
              "joint_7": (-90.0, 90.0), "gripper": (-65.0, 0.0)},
}
PORTS = {"right": 5701, "left": 5702}       # run_teleop_remote_{right,left}.sh
ECHO_PORTS = {"right": 5711, "left": 5712}


@dataclass
class ValidatorConfig:
    margin_deg: float = 2.0                  # arm joints stay this far inside the follower limits
    # The gripper keeps the full follower range: 0 is its calibrated closed pose and the start pose.
    max_joint_speed_dps: float = 60.0        # per-joint, enforced per cycle as speed * dt
    max_gripper_speed_dps: float = 150.0
    dt_slack: float = 1.5                    # allow late cycles up to this factor of the nominal dt


@dataclass
class Verdict:
    ok: bool
    reasons: list[str] = field(default_factory=list)


class ActionValidator:
    """Checks one arm's action dict. `prev` is the last action that was actually sent (or None)."""

    def __init__(self, side: str, hz: float, cfg: ValidatorConfig | None = None):
        if side not in PORTS:
            raise ValueError(f"side must be left/right, got {side!r}")
        self.side, self.cfg = side, cfg or ValidatorConfig()
        self.dt = 1.0 / hz
        m = self.cfg.margin_deg
        self.limits = {f"{k}.pos": (lo, hi) if k == "gripper" else (lo + m, hi - m)
                       for k, (lo, hi) in FOLLOWER_LIMITS_DEG[side].items()}

    def check(self, action: dict, prev: dict | None, arm: str | None = None) -> Verdict:
        r: list[str] = []
        if arm is not None and arm != self.side:
            r.append(f"packet arm {arm!r} != validator side {self.side!r}")
        keys = set(action)
        missing = [k for k in ACTION_KEYS if k not in keys]
        extra = sorted(keys - set(ACTION_KEYS))
        if missing:
            r.append(f"missing keys {missing}")
        if extra:
            r.append(f"unexpected keys {extra}")
        for k in ACTION_KEYS:
            if k not in action:
                continue
            v = action[k]
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
                r.append(f"{k}={v!r} is not a finite number")
                continue
            lo, hi = self.limits[k]
            if not lo <= v <= hi:
                r.append(f"{k}={v:.2f} outside [{lo:.1f}, {hi:.1f}] deg")
            if prev is not None and k in prev and math.isfinite(prev[k]):
                speed = self.cfg.max_gripper_speed_dps if k == "gripper.pos" else self.cfg.max_joint_speed_dps
                lim = speed * self.dt * self.cfg.dt_slack
                if abs(v - prev[k]) > lim + 1e-9:
                    r.append(f"{k} step {v - prev[k]:+.2f} deg > {lim:.2f} deg/cycle")
        return Verdict(not r, r)


def looks_like_radians(action: dict) -> bool:
    """Heuristic guard: every joint within +-pi while the arm is clearly bent is suspicious.

    Only used as a warning in the sender; the limit check is the real guard.
    """
    vals = [abs(action[k]) for k in ACTION_KEYS[:7] if k in action]
    return bool(vals) and max(vals) <= math.pi and action.get("joint_4.pos", 0.0) > 0.0 and max(vals) > 0.3
