"""Per-arm OpenArm kinematics (pinocchio) for the follower: FK and position-only IK with a TCP.

Each side is built from its OWN chain in the bimanual URDF (openarm_<side>_joint1..7 and
openarm_<side>_hand_tcp). The right arm is not a sign-flipped left arm: e.g. right joint7 is a
pure Y axis while left joint7 is not (URDF, checked 2026-10-31, log-002).

Units: pinocchio uses radians and metres in the URDF world frame (+x forward, +y left, +z up).
lerobot uses degrees with the same zero and sign (follower joint_limits equal the URDF limits).
Interface is independent of the PICO so a future leader-arm mode can reuse it.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

import numpy as np
import pinocchio as pin

DEFAULT_URDF = os.path.expanduser("~/projects/openarm-orboh/urdf/openarm_v10_bimanual.urdf")
# The URDF's openarm_<side>_hand_tcp sits ON link7 with a zero offset (URDF lines 474-478 / 547-551):
# it is the wrist point, and joints 5-7 cannot move it (Elephant review, log-005). The gripper points
# along link7 +z and the fingers are mounted 0.1025 m out (finger_joint origin). We add our own TCP
# frame there. 0.1025 m is the finger ROOT, a lower bound: measure the real grasp point and pass it.
DEFAULT_TCP_OFFSET_M = 0.1025
LEROBOT_JOINTS = [f"joint_{i}" for i in range(1, 8)]


@dataclass(frozen=True)
class IKResult:
    q: np.ndarray        # rad, 7
    converged: bool      # position error <= tol
    err_m: float         # final TCP position error
    iters: int
    sigma_min: float     # smallest singular value of the 3x7 position Jacobian at q (singularity)


def file_sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


class ArmKinematics:
    """FK / IK for one arm. `limits_deg` (lerobot joint_k -> (lo, hi)) further restricts the URDF."""

    def __init__(self, side: str, urdf_path: str = DEFAULT_URDF,
                 limits_deg: dict[str, tuple[float, float]] | None = None, margin_deg: float = 0.0,
                 tcp_offset_m: float = DEFAULT_TCP_OFFSET_M):
        if side not in ("left", "right"):
            raise ValueError(f"side must be left/right, got {side!r}")
        self.side = side
        self.urdf_path = urdf_path
        self.urdf_sha = file_sha256(urdf_path)
        joints = [f"openarm_{side}_joint{i}" for i in range(1, 8)]
        full = pin.buildModelFromUrdf(urdf_path)
        lock = [j for j in range(1, full.njoints) if full.names[j] not in joints]
        self.model = pin.buildReducedModel(full, lock, pin.neutral(full))
        self.data = self.model.createData()
        if [self.model.names[j] for j in range(1, self.model.njoints)] != joints:
            raise RuntimeError(f"unexpected joint order in reduced model: {list(self.model.names)}")
        link7 = self.model.frames[self.model.getFrameId(f"openarm_{side}_link7")]
        self.tcp_offset_m = float(tcp_offset_m)
        placement = link7.placement * pin.SE3(np.eye(3), np.array([0.0, 0.0, self.tcp_offset_m]))
        self.tcp_fid = self.model.addFrame(pin.Frame(f"pico_{side}_tcp", link7.parentJoint,
                                                     self.model.getFrameId(f"openarm_{side}_link7"),
                                                     placement, pin.FrameType.OP_FRAME))
        self.data = self.model.createData()
        lo = np.array(self.model.lowerPositionLimit, dtype=float)
        hi = np.array(self.model.upperPositionLimit, dtype=float)
        if limits_deg:
            for i, j in enumerate(LEROBOT_JOINTS):
                if j in limits_deg:
                    lo[i] = max(lo[i], np.deg2rad(limits_deg[j][0]))
                    hi[i] = min(hi[i], np.deg2rad(limits_deg[j][1]))
        m = np.deg2rad(margin_deg)
        self.lo, self.hi = lo + m, hi - m
        if np.any(self.lo >= self.hi):
            raise ValueError("joint limits empty after margin")

    # --- FK -------------------------------------------------------------------------------
    def fk(self, q) -> pin.SE3:
        q = np.asarray(q, dtype=float)
        pin.forwardKinematics(self.model, self.data, q)
        pin.updateFramePlacement(self.model, self.data, self.tcp_fid)
        return self.data.oMf[self.tcp_fid].copy()

    def jac_pos(self, q) -> np.ndarray:
        """3x7 TCP position Jacobian, world-aligned."""
        J = pin.computeFrameJacobian(self.model, self.data, np.asarray(q, float), self.tcp_fid,
                                     pin.ReferenceFrame.LOCAL_WORLD_ALIGNED)
        return J[:3, :]

    def sigma_min(self, q) -> float:
        return float(np.linalg.svd(self.jac_pos(q), compute_uv=False)[-1])

    # --- IK -------------------------------------------------------------------------------
    def solve_position(self, p_target, q_seed, tol_m: float = 1e-3, max_iter: int = 60,
                       damp: float = 1e-3, max_dq: float = 0.2, limit_band_deg: float = 0.0,
                       null_gain: float = 0.0) -> IKResult:
        """Damped least squares toward a TCP position, orientation free, projected onto joint limits.

        Seeded with the previous solution so consecutive targets stay on the same branch.
        `max_dq` (rad) bounds each inner step, which keeps the iteration from jumping branches.
        With `limit_band_deg` and `null_gain` > 0, a joint closer than the band to a limit is pushed
        away from it in the Jacobian null space (4 redundant DOF: 7 joints, 3D position). The TCP is
        unaffected; the arm uses its redundancy before a joint saturates, instead of clamping and then
        swinging another joint. Zero push in the middle of the range: a still hand keeps a still arm.
        """
        p_tgt = np.asarray(p_target, dtype=float)
        q = self.clamp(q_seed)
        err = np.inf
        for it in range(max_iter):
            e = self.fk(q).translation - p_tgt
            err = float(np.linalg.norm(e))
            if err <= tol_m:
                return IKResult(q, True, err, it, self.sigma_min(q))
            J = self.jac_pos(q)
            Jp = J.T @ np.linalg.inv(J @ J.T + damp * np.eye(3))
            dq = -Jp @ e
            if limit_band_deg > 0.0 and null_gain > 0.0:
                dq += (np.eye(len(q)) - Jp @ J) @ (null_gain * self.limit_push(q, limit_band_deg))
            n = np.max(np.abs(dq))
            if n > max_dq:
                dq *= max_dq / n
            q = self.clamp(q + dq)
        err = float(np.linalg.norm(self.fk(q).translation - p_tgt))
        return IKResult(q, err <= tol_m, err, max_iter, self.sigma_min(q))

    # --- limits and unit bridges ----------------------------------------------------------
    def limit_push(self, q, band_deg: float) -> np.ndarray:
        """Per joint in [-1, 1]: +1 at the lower limit, -1 at the upper, 0 beyond `band_deg` from both."""
        b = np.deg2rad(band_deg)
        q = np.asarray(q, float)
        lo = np.clip((b - (q - self.lo)) / b, 0.0, 1.0)
        hi = np.clip((b - (self.hi - q)) / b, 0.0, 1.0)
        return lo - hi

    def clamp(self, q) -> np.ndarray:
        return np.clip(np.asarray(q, float), self.lo, self.hi)

    def within_limits(self, q, tol: float = 1e-9) -> bool:
        q = np.asarray(q, float)
        return bool(np.all(q >= self.lo - tol) and np.all(q <= self.hi + tol))


def deg_dict_to_q(d: dict[str, float]) -> np.ndarray:
    """lerobot {'joint_k' or 'joint_k.pos': deg} -> rad array (7)."""
    get = lambda j: d[f"{j}.pos"] if f"{j}.pos" in d else d[j]
    return np.deg2rad([float(get(j)) for j in LEROBOT_JOINTS])


def q_to_action(q_rad, gripper_deg: float) -> dict[str, float]:
    """rad array -> the receiver's action dict (degrees, joint_1.pos..joint_7.pos, gripper.pos)."""
    deg = np.rad2deg(np.asarray(q_rad, float))
    act = {f"{j}.pos": float(deg[i]) for i, j in enumerate(LEROBOT_JOINTS)}
    act["gripper.pos"] = float(gripper_deg)
    return act
