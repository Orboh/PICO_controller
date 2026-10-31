"""Gate 2 visual evidence: run synthetic (or recorded) hand trajectories through ArmTeleop and plot
the TCP path, the joint angles against limits, per-cycle steps against the limits, state and gripper.

    .venv/bin/python -m pico_teleop.plot_offline --out evidence/gate2            # synthetic, both arms
    .venv/bin/python -m pico_teleop.plot_offline --replay runs/diag-X.jsonl       # a gate-1 recording

Each plot's title says PASS or FAIL: PASS = no validator reject, no step over the controller limit,
and the run ended as expected (tracking runs stay ENGAGED; "reach" must end in FAULT, not a jump).
"""
from __future__ import annotations

import argparse
import math
import os
import textwrap

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .controller import ENGAGED, FAULT, ArmTeleop, PicoAdapter, TeleopConfig  # noqa: E402
from .frames import R_ROBOT_FROM_XR  # noqa: E402
from .kinematics import ArmKinematics  # noqa: E402
from .poses import current_pose_set, load_start_pose  # noqa: E402
from .validate import FOLLOWER_LIMITS_DEG, ActionValidator, ValidatorConfig  # noqa: E402
from .xr_input import ControllerSample, FreshnessTracker  # noqa: E402

STATE_LEVEL = {"HOLD": 0, ENGAGED: 1, FAULT: -1}


def synthetic(name: str, hz: float, seconds: float = 8.0):
    """ControllerSample in OpenXR; grip released for the first 0.1 s (arms the clutch); tiny noise."""
    xr0 = np.array([0.2, 1.0, -0.4])
    for i in range(int(seconds * hz)):
        t = i / hz
        if name == "circle":
            d = np.array([0.1 * math.cos(t) - 0.1, 0.1 * math.sin(t), 0.0])
        elif name == "square":
            k, u = int(t // 2) % 4, (t % 2) / 2
            corners = np.array([[0, 0, 0], [0.15, 0, 0], [0.15, 0.15, 0], [0, 0.15, 0], [0, 0, 0]])
            d = corners[k] + u * (corners[k + 1] - corners[k])
        else:  # "reach": push 2 m forward, beyond reach -> must FAULT, not jump
            d = np.array([min(t, 4.0) * 0.5, 0.0, 0.0])
        d = d + 1e-5 * np.array([i % 7, i % 5, i % 3])
        p = xr0 + R_ROBOT_FROM_XR.T @ d
        yield ControllerSample(tuple(p) + (0.0, 0.0, 0.0, 1.0), 0.0 if t < 0.1 else 1.0, 1.0)


def run(side: str, samples, cfg: TeleopConfig, start: dict):
    kin = ArmKinematics(side, limits_deg=FOLLOWER_LIMITS_DEG[side], margin_deg=cfg.joint_margin_deg)
    arm, adapt = ArmTeleop(side, kin, start, cfg), PicoAdapter(cfg.operator_yaw_deg)
    val = ActionValidator(side, cfg.hz, ValidatorConfig(margin_deg=cfg.joint_margin_deg))
    rows, prev, events = [], None, []
    for i, (c, fresh) in enumerate(samples):
        out = arm.step(adapt(c, fresh))
        ok = val.check(out.action, prev, side).ok
        prev = out.action
        q = np.deg2rad([out.action[f"joint_{j}.pos"] for j in range(1, 8)])
        tcp = kin.fk(q).translation
        tgt = arm.p_target if arm.p_target is not None else tcp
        rows.append((i / cfg.hz, *tcp, *tgt, *np.rad2deg(q), out.action["gripper.pos"],
                     STATE_LEVEL[out.state], ok))
        if out.event:
            events.append((round(i / cfg.hz, 2), out.event))
    return np.array(rows, dtype=float), events


def verdict(name: str, data: np.ndarray, cfg: TeleopConfig) -> tuple[bool, str]:
    step = np.max(np.abs(np.diff(data[:, 7:14], axis=0))) if len(data) > 1 else 0.0
    lim = cfg.max_joint_speed_dps / cfg.hz
    rejects = int((1 - data[:, 16]).sum())
    ended = {1: "ENGAGED", 0: "HOLD", -1: "FAULT"}[int(data[-1, 15])]
    want = "FAULT" if name == "reach" else "ENGAGED"
    ok = rejects == 0 and step <= lim + 1e-6 and (ended == want or name == "replay")
    return ok, (f"{'PASS' if ok else 'FAIL'}: rejects {rejects}, max step {step:.2f} deg (limit {lim:.2f}), "
                f"ended {ended} (expected {want if name != 'replay' else 'any'})")


def plot(side: str, name: str, data: np.ndarray, events, cfg: TeleopConfig, pose_set: str, out_dir: str) -> str:
    ok, text = verdict(name, data, cfg)
    fig, ax = plt.subplots(1, 4, figsize=(20, 5))
    ax[0].plot(data[:, 4], data[:, 5], "--", label="TCP target (rate-limited)")
    ax[0].plot(data[:, 1], data[:, 2], label="TCP achieved (FK of sent joints)")
    ax[0].set(xlabel="robot x forward [m]", ylabel="robot y left [m]", title="TCP top view")
    ax[0].axis("equal"); ax[0].legend(fontsize=8)
    lim = FOLLOWER_LIMITS_DEG[side]
    for j in range(7):
        line, = ax[1].plot(data[:, 0], data[:, 7 + j], label=f"joint_{j + 1}")
        lo, hi = lim[f"joint_{j + 1}"]
        lo, hi = lo + cfg.joint_margin_deg, hi - cfg.joint_margin_deg
        span = data[:, 7 + j]
        for b in (lo, hi):
            if abs(span - b).min() < 20:
                ax[1].axhline(b, color=line.get_color(), ls=":", lw=1)
                ax[1].text(data[-1, 0], b, f" j{j + 1} limit", color=line.get_color(), fontsize=7, va="center")
    ax[1].set(xlabel="t [s]", ylabel="deg", title="joint targets (dotted: limit within 20 deg)")
    ax[1].legend(fontsize=7, ncol=2)
    step = np.max(np.abs(np.diff(data[:, 7:14], axis=0)), axis=1)
    ax[2].plot(data[1:, 0], step, label="max joint step per cycle")
    ax[2].axhline(cfg.max_joint_speed_dps / cfg.hz, color="tab:orange", ls="--", label="controller limit")
    ax[2].axhline(60.0 / cfg.hz * 1.5, color="tab:red", ls="--", label="validator limit")
    ax[2].set(xlabel="t [s]", ylabel="deg / cycle", title="continuity"); ax[2].legend(fontsize=8)
    ax[3].step(data[:, 0], data[:, 15], where="post", label="state")
    ax[3].set_yticks([-1, 0, 1], ["FAULT", "HOLD", "ENGAGED"])
    ax3 = ax[3].twinx()
    ax3.plot(data[:, 0], data[:, 14], color="tab:green", label="gripper deg")
    ax3.set_ylabel("gripper [deg] (0 closed, -60 open)")
    ax[3].set(xlabel="t [s]", title="state and gripper")
    title = f"[{text}]  {side} {name}, pose set {pose_set}\n" + "\n".join(
        textwrap.shorten(f"{t}s {e}", 150) for t, e in events[:3])
    fig.suptitle(title, fontsize=9, color="green" if ok else "red")
    fig.tight_layout()
    path = os.path.join(out_dir, f"gate2-{side}-{name}.png")
    fig.savefig(path, dpi=85)
    plt.close(fig)
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="runs/plots")
    ap.add_argument("--replay", default=None, help="gate-1 recording (diag-*.jsonl), all phases in order")
    ap.add_argument("--side", choices=["left", "right", "both"], default="both")
    ap.add_argument("--pose-set", default=None, help="default: openarm-orboh pose_sets/CURRENT")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    cfg, pose_set = TeleopConfig(), a.pose_set or current_pose_set()
    all_ok = True
    for side in (["left", "right"] if a.side == "both" else [a.side]):
        start = load_start_pose(side, pose_set)
        if a.replay:
            from .diag_input import load_replay
            tr, frames = FreshnessTracker(), [f for _, fr, _ in load_replay(a.replay) for f in fr]

            def gen(frames=frames, side=side):
                for f in frames:
                    tr.update(f)
                    yield f.controller(side), tr.fresh(side)
            jobs = [("replay", gen())]
        else:
            jobs = [(n, ((c, True) for c in synthetic(n, cfg.hz))) for n in ("circle", "square", "reach")]
        for name, samples in jobs:
            data, events = run(side, samples, cfg, start)
            ok, text = verdict(name, data, cfg)
            all_ok &= ok
            path = plot(side, name, data, events, cfg, pose_set, a.out)
            print(f"{side:5s} {name:7s} {text}  events {[e[1][:70] for e in events[:3]]}  -> {path}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
