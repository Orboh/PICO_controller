"""Gate 2 visual evidence: run synthetic (or recorded) hand trajectories through ArmTeleop and plot
the desired vs achieved TCP path and the joint angles against the validator limits.

    .venv/bin/python -m pico_teleop.plot_offline --out runs/plots            # synthetic, both arms
    .venv/bin/python -m pico_teleop.plot_offline --replay runs/diag-X.jsonl  # a gate-1 recording
"""
from __future__ import annotations

import argparse
import math
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .controller import ENGAGED, ArmTeleop, TeleopConfig  # noqa: E402
from .frames import R_ROBOT_FROM_XR  # noqa: E402
from .kinematics import ArmKinematics  # noqa: E402
from .validate import FOLLOWER_LIMITS_DEG, ActionValidator  # noqa: E402
from .xr_input import ControllerSample, FreshnessTracker  # noqa: E402

START = {  # takeei-v2 start poses (also in tests/test_gate2_ik_mapping.py)
    "left": {"joint_1": 54.43, "joint_2": -1.21, "joint_3": -1.58, "joint_4": 116.91, "joint_5": 24.12,
             "joint_6": -8.09, "joint_7": -46.50, "gripper": 0.0},
    "right": {"joint_1": -53.07, "joint_2": -0.68, "joint_3": 0.34, "joint_4": 132.21, "joint_5": -23.63,
              "joint_6": 12.61, "joint_7": 8.45, "gripper": 0.0},
}


def synthetic(name: str, hz: float, seconds: float = 8.0):
    """Yields ControllerSample in OpenXR; grip released for the first 0.1 s (arms the clutch)."""
    xr0 = np.array([0.2, 1.0, -0.4])
    for i in range(int(seconds * hz)):
        t = i / hz
        if name == "circle":
            d = np.array([0.1 * math.cos(t) - 0.1, 0.1 * math.sin(t), 0.0])
        elif name == "square":
            k, u = int(t // 2) % 4, (t % 2) / 2
            corners = np.array([[0, 0, 0], [0.15, 0, 0], [0.15, 0.15, 0], [0, 0.15, 0], [0, 0, 0]])
            d = corners[k] + u * (corners[k + 1] - corners[k])
        else:  # "reach": push 1 m forward, beyond reach -> must FAULT, not jump
            d = np.array([min(t, 4.0) * 0.5, 0.0, 0.0])
        p = xr0 + R_ROBOT_FROM_XR.T @ d
        yield ControllerSample(tuple(p) + (0.0, 0.0, 0.0, 1.0), 0.0 if t < 0.1 else 1.0, 0.5)


def run(side: str, samples, cfg: TeleopConfig):
    kin = ArmKinematics(side, limits_deg=FOLLOWER_LIMITS_DEG[side], margin_deg=cfg.joint_margin_deg)
    arm = ArmTeleop(side, kin, {f"{k}.pos": v for k, v in START[side].items()}, cfg)
    val = ActionValidator(side, cfg.hz)
    rows, prev, events = [], None, []
    for i, (c, fresh) in enumerate(samples):
        out = arm.step(c, fresh)
        ok = val.check(out.action, prev, side).ok
        prev = out.action
        tcp = kin.fk(np.deg2rad([out.action[f"joint_{j}.pos"] for j in range(1, 8)])).translation
        tgt = arm.p_target if arm.p_target is not None else tcp
        rows.append((i / cfg.hz, *tcp, *tgt, *[out.action[f"joint_{j}.pos"] for j in range(1, 8)],
                     out.action["gripper.pos"], out.state == ENGAGED, ok))
        if out.event:
            events.append((round(i / cfg.hz, 2), out.event))
    return np.array(rows, dtype=float), events


def plot(side: str, name: str, data: np.ndarray, events, out_dir: str) -> str:
    fig, ax = plt.subplots(1, 3, figsize=(16, 4.5))
    ax[0].plot(data[:, 4], data[:, 5], "--", label="TCP target (rate-limited)")
    ax[0].plot(data[:, 1], data[:, 2], label="TCP achieved (FK of sent joints)")
    ax[0].set(xlabel="robot x forward [m]", ylabel="robot y left [m]", title=f"{side} {name}: TCP top view")
    ax[0].axis("equal"); ax[0].legend(fontsize=8)
    lim = FOLLOWER_LIMITS_DEG[side]
    for j in range(7):
        line, = ax[1].plot(data[:, 0], data[:, 7 + j], label=f"joint_{j + 1}")
        lo, hi = lim[f"joint_{j + 1}"]
        span = data[:, 7 + j]
        if span.min() - 15 < lo or span.max() + 15 > hi:
            ax[1].axhline(lo if span.min() - 15 < lo else hi, color=line.get_color(), ls=":", lw=0.8)
    ax[1].set(xlabel="t [s]", ylabel="deg", title="joint targets (dotted: nearby limits)")
    ax[1].legend(fontsize=7, ncol=2)
    step = np.max(np.abs(np.diff(data[:, 7:14], axis=0)), axis=1) if len(data) > 1 else np.zeros(1)
    ax[2].plot(data[1:, 0], step, label="max joint step per cycle [deg]")
    ax[2].plot(data[:, 0], data[:, 15] * 1.0, label="ENGAGED (1) / not (0)")
    ax[2].plot(data[:, 0], 1 - data[:, 16], label="validator reject (1)")
    ax[2].set(xlabel="t [s]", title="continuity and state"); ax[2].legend(fontsize=8)
    fig.suptitle("; ".join(f"{t}s {e[:60]}" for t, e in events[:3]), fontsize=8)
    fig.tight_layout()
    path = os.path.join(out_dir, f"gate2-{side}-{name}.png")
    fig.savefig(path, dpi=90)
    plt.close(fig)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="runs/plots")
    ap.add_argument("--replay", default=None, help="gate-1 recording (diag-*.jsonl), all phases in order")
    ap.add_argument("--side", choices=["left", "right", "both"], default="both")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    cfg = TeleopConfig()
    sides = ["left", "right"] if a.side == "both" else [a.side]
    for side in sides:
        if a.replay:
            from .diag_input import load_replay
            tr = FreshnessTracker()
            frames = [f for _, fr, _ in load_replay(a.replay) for f in fr]

            def gen(frames=frames, side=side):
                for f in frames:
                    tr.update(f)
                    yield f.controller(side), tr.fresh(side)
            jobs = [("replay", gen())]
        else:
            jobs = [(n, ((c, True) for c in synthetic(n, cfg.hz))) for n in ("circle", "square", "reach")]
        for name, samples in jobs:
            data, events = run(side, samples, cfg)
            path = plot(side, name, data, events, a.out)
            print(f"{side:5s} {name:7s} engaged {data[:, 15].mean() * 100:5.1f}%  validator rejects "
                  f"{int((1 - data[:, 16]).sum())}  max step {np.max(np.abs(np.diff(data[:, 7:14], axis=0))):.2f} deg  "
                  f"events {events[:3]}  -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
