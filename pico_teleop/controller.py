"""Per-arm clutch state machine: controller pose -> TCP target -> position-only IK -> joint target.

Pure logic (no SDK, no sockets, time passed in) so it is tested offline and can be driven later by
another input device (e.g. a leader-arm mode) through `step()`.

States
- HOLD    : not following. The last safe target is kept (and keeps being sent by the sender).
- ENGAGED : grip held. TCP target = anchor TCP + scale * R * (controller - anchor controller).
- FAULT   : something went wrong while engaged. Targets frozen until grip is RELEASED, then HOLD;
            a new squeeze re-anchors. Causes: input invalid / stale / frozen, IK not converged
            (unreachable), near singular, IK branch jump.
A squeeze that is already held when the sender starts does not engage: the grip must be seen
released once first ("armed").
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .frames import controller_in_robot
from .kinematics import ArmKinematics, deg_dict_to_q, q_to_action
from .xr_input import ControllerSample, pose_status

HOLD, ENGAGED, FAULT = "HOLD", "ENGAGED", "FAULT"


@dataclass
class TeleopConfig:
    """Every number here is a starting value to be confirmed on hardware (log-004 gives the basis)."""

    hz: float = 50.0
    scale: float = 0.5                 # hand metre -> TCP metre; start small
    clutch_on: float = 0.6             # grip >= this engages ...
    clutch_off: float = 0.4            # ... and < this releases (hysteresis)
    gripper_open_deg: float = -60.0    # trigger released (lerobot gripper range is -65..0, 0 = closed)
    gripper_closed_deg: float = 0.0    # trigger fully pulled
    max_tcp_speed_mps: float = 0.25    # TCP target rate limit
    max_joint_speed_dps: float = 45.0  # joint rate limit applied before validation (validator: 60)
    max_gripper_speed_dps: float = 120.0
    ik_solve_tol_m: float = 1e-4       # IK iterates to this every cycle (smooth, no 5 mm staircase)
    ik_tol_m: float = 0.005            # accepted if the answer is within 5 mm, else "unreachable"
    ik_max_iter: int = 60
    min_sigma: float = 0.03            # smallest singular value of the 3x7 position Jacobian
    max_ik_jump_deg: float = 20.0      # IK answer vs the previous IK answer: larger = branch flip
    limit_band_deg: float = 15.0       # joints closer than this to a limit are pushed away (null space)
    null_gain: float = 0.0             # OFF by default (log-004: pushing joint_2 off its limit
                                       # moved the arm at engage and swung joint_2 75 deg); rad per step
    max_lag_deg: float = 10.0          # sent joints may trail the IK answer by this much; beyond it
                                       # the TCP target waits for the joints to catch up
    operator_yaw_deg: float = 0.0      # operator facing direction relative to robot +x
    joint_margin_deg: float = 2.0


@dataclass
class StepOut:
    action: dict[str, float]
    state: str
    event: str | None = None
    ik_err_m: float = 0.0
    sigma: float = math.nan


@dataclass
class ArmTeleop:
    side: str
    kin: ArmKinematics
    start_action: dict[str, float]     # verified follower pose (degrees, lerobot keys)
    cfg: TeleopConfig = field(default_factory=TeleopConfig)

    def __post_init__(self):
        self.q = deg_dict_to_q(self.start_action)
        if not self.kin.within_limits(self.q, tol=1e-6):
            raise ValueError(f"{self.side}: start pose outside joint limits (with margin)")
        self.gripper = float(self.start_action["gripper.pos"] if "gripper.pos" in self.start_action
                             else self.start_action["gripper"])
        self.state, self.armed = HOLD, False
        self.anchor_ctrl: np.ndarray | None = None
        self.anchor_tcp: np.ndarray | None = None
        self.p_target: np.ndarray | None = None
        self.q_ik = self.q.copy()          # last IK answer; self.q (sent) trails it by the rate limit
        self.grip_synced = False
        self.dt = 1.0 / self.cfg.hz

    # ------------------------------------------------------------------------------------
    def action(self) -> dict[str, float]:
        return q_to_action(self.q, self.gripper)

    def _out(self, event=None, err=0.0, sigma=math.nan) -> StepOut:
        return StepOut(self.action(), self.state, event, err, sigma)

    def _fault(self, why: str) -> StepOut:
        self.state = FAULT
        return self._out(f"FAULT: {why} (targets frozen; release grip, then squeeze to re-anchor)")

    def force_fault(self, why: str) -> StepOut:
        """Called by the sender for faults outside this class (validator, link)."""
        if self.state == ENGAGED:
            return self._fault(why)
        return self._out()

    def step(self, c: ControllerSample, fresh: bool, stale_reason: str = "input not fresh") -> StepOut:
        cfg = self.cfg
        grip = c.grip if math.isfinite(c.grip) else 0.0
        released = grip < cfg.clutch_off
        if released:
            self.armed = True

        if self.state == FAULT:
            if released:
                self.state = HOLD
                return self._out("fault cleared by grip release (HOLD)")
            return self._out()

        valid = fresh and pose_status(c.pose) == "ok"
        if self.state == ENGAGED and not valid:
            return self._fault(stale_reason if not fresh else f"pose {pose_status(c.pose)}")
        if self.state == ENGAGED and released:
            self.state = HOLD
            return self._out("released: HOLD at last target")

        if self.state == HOLD:
            if not (valid and self.armed and grip >= cfg.clutch_on):
                return self._out()
            pos, _ = controller_in_robot(c.pose, cfg.operator_yaw_deg)
            self.anchor_ctrl = pos
            self.anchor_tcp = self.kin.fk(self.q).translation.copy()
            self.p_target = self.anchor_tcp.copy()
            self.q_ik = self.q.copy()
            self.state = ENGAGED
            self.grip_synced = False
            return self._out("ENGAGED: anchored at the current target (no jump)")

        # ENGAGED and valid: follow
        err, sigma = 0.0, math.nan
        lag = float(np.max(np.abs(np.rad2deg(self.q_ik - self.q))))
        if lag <= cfg.max_lag_deg:  # otherwise the target waits while the joints catch up
            pos, _ = controller_in_robot(c.pose, cfg.operator_yaw_deg)
            p_des = self.anchor_tcp + cfg.scale * (pos - self.anchor_ctrl)
            d = p_des - self.p_target
            lim = cfg.max_tcp_speed_mps * self.dt
            n = float(np.linalg.norm(d))
            p_tgt = self.p_target + (d * (lim / n) if n > lim else d)
            res = self.kin.solve_position(p_tgt, self.q_ik, tol_m=cfg.ik_solve_tol_m, max_iter=cfg.ik_max_iter,
                                         limit_band_deg=cfg.limit_band_deg, null_gain=cfg.null_gain)
            if res.err_m > cfg.ik_tol_m:
                return self._fault(f"IK did not converge ({res.err_m * 1000:.1f} mm): target unreachable")
            if res.sigma_min < cfg.min_sigma:
                return self._fault(f"near singular (sigma {res.sigma_min:.3f} < {cfg.min_sigma})")
            jump = float(np.max(np.abs(np.rad2deg(res.q - self.q_ik))))
            if jump > cfg.max_ik_jump_deg:
                return self._fault(f"IK jumped {jump:.1f} deg (> {cfg.max_ik_jump_deg}): branch flip")
            self.p_target, self.q_ik, err, sigma = p_tgt, res.q, res.err_m, res.sigma_min
        qlim = math.radians(cfg.max_joint_speed_dps) * self.dt
        self.q = self.kin.clamp(self.q + np.clip(self.q_ik - self.q, -qlim, qlim))

        t = min(max(c.trigger if math.isfinite(c.trigger) else 0.0, 0.0), 1.0)
        g_des = cfg.gripper_open_deg + t * (cfg.gripper_closed_deg - cfg.gripper_open_deg)
        # Soft takeover: the gripper stays put until the trigger's mapped value reaches it, so
        # engaging with a released trigger does not suddenly open a closed gripper.
        if not self.grip_synced:
            lo, hi = sorted((cfg.gripper_open_deg, cfg.gripper_closed_deg))
            g_now = min(max(self.gripper, lo), hi)
            if abs(g_des - g_now) <= 3.0:
                self.grip_synced = True
        if self.grip_synced:
            glim = cfg.max_gripper_speed_dps * self.dt
            self.gripper += float(np.clip(g_des - self.gripper, -glim, glim))
        return self._out(None, err, sigma)
