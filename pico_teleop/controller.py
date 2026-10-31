"""Per-arm clutch state machine: hand position -> TCP target -> position-only IK -> joint target.

Device-independent core (`ArmTeleop.step(HandInput)`) plus a PICO adapter (`PicoAdapter`). A future
leader-arm or other device only needs its own adapter that fills `HandInput`. No SDK, no sockets.

States (operator meaning in README "Controls")
- HOLD    : not following. The last SENT target is kept and keeps being sent.
- ENGAGED : clutch held. TCP target = anchor TCP + scale * (hand - anchor hand), in the robot frame.
- FAULT   : something went wrong while engaged. Targets frozen until the clutch is RELEASED (on a
            valid sample), then HOLD; a new squeeze re-anchors. Causes: invalid / stale / frozen input,
            invalid grip or trigger value, IK unreachable, near singular, IK jump, posture change.
"Armed": a clutch already held when the sender starts does not engage; a release must be seen on a
VALID sample first (an all-zero SDK frame reads grip 0 and must not count as a release).
Engaging needs the clutch held for `engage_hold_s` (0.3 s) on valid samples.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .frames import controller_in_robot
from .kinematics import ArmKinematics, deg_dict_to_q, q_to_action
from .xr_input import ControllerSample, analog_ok, pose_status

HOLD, ENGAGED, FAULT = "HOLD", "ENGAGED", "FAULT"


@dataclass
class TeleopConfig:
    """Every number here is a starting value to be confirmed on hardware (log-004/005 give the basis)."""

    hz: float = 50.0
    scale: float = 0.5                 # hand metre -> TCP metre; start small
    clutch_on: float = 0.6             # grip >= this engages ...
    clutch_off: float = 0.4            # ... and < this releases (hysteresis: squeeze firmly, open fully)
    engage_hold_s: float = 0.3         # grip must stay squeezed this long before ENGAGE (log-008:
                                       # accidental grip while pulling the trigger lasts < 50 ms)
    max_hand_speed_mps: float = 3.0    # input faster than this between device samples = tracking
                                       # catch-up (live: held <= 2.0 m/s, hidden-controller return
                                       # 4.2-42 m/s; log-008) -> FAULT
    max_target_lag_m: float = 0.15     # desired TCP this far ahead of the rate-limited target -> FAULT
    max_hand_step_m: float = 0.10      # hand moved this far in ONE device update -> FAULT, whatever the
                                       # dt (a catch-up after a short gap can stay under the speed limit)
    gripper_open_deg: float = -60.0    # trigger released (lerobot gripper range is -65..0, 0 = closed)
    gripper_closed_deg: float = 0.0    # trigger fully pulled
    max_tcp_speed_mps: float = 0.25    # TCP target rate limit
    max_joint_speed_dps: float = 45.0  # joint rate limit applied before validation (validator: 60)
    max_gripper_speed_dps: float = 120.0
    ik_solve_tol_m: float = 1e-4       # IK iterates to this every cycle (smooth, no 5 mm staircase)
    ik_tol_m: float = 0.005            # accepted if the answer is within 5 mm, else "unreachable"
    ik_max_iter: int = 60
    min_sigma: float = 0.03            # smallest singular value of the 3x7 position Jacobian
    max_ik_jump_deg: float = 5.0       # IK answer vs previous IK answer per cycle (TCP moves <= 5 mm)
    max_posture_change_deg: float = 60.0  # any joint this far from its value at engage -> FAULT
                                          # (45 -> 60 on 2026-10-31, Suraj: 45 tripped in normal large
                                          # moves in the first arm session, log-010)
    limit_band_deg: float = 15.0       # joints closer than this to a limit are pushed away (null space)
    null_gain: float = 0.0             # OFF (log-004: it moved the arm at engage); rad per IK step
    max_lag_deg: float = 10.0          # sent joints may trail the IK answer by this much; beyond it
                                       # the TCP target waits for the joints to catch up
    operator_yaw_deg: float = 0.0      # operator facing direction relative to robot +x
    joint_margin_deg: float = 2.0


@dataclass(frozen=True)
class HandInput:
    """What the core needs from any input device, already validated by its adapter."""

    valid: bool                  # pose, clutch and gripper values usable this cycle
    pos: np.ndarray | None       # hand position in the ROBOT frame [m] (relative motion is used)
    clutch: float                # 0..1 (PICO: grip)
    gripper: float               # 0..1, 0 = open, 1 = closed (PICO: trigger)
    why_invalid: str = ""
    t: float | None = None       # sample time on the DEVICE clock [s] (None: speed check skipped)


class PicoAdapter:
    """ControllerSample (OpenXR) + freshness -> HandInput."""

    def __init__(self, operator_yaw_deg: float = 0.0):
        self.yaw = operator_yaw_deg

    def __call__(self, c: ControllerSample, fresh: bool, why_stale: str = "input not fresh",
                 t_device: float | None = None) -> HandInput:
        ps = pose_status(c.pose)
        if not fresh:
            return HandInput(False, None, 0.0, 0.0, why_stale)
        if ps != "ok":
            return HandInput(False, None, 0.0, 0.0, f"pose {ps}")
        if not (analog_ok(c.grip) and analog_ok(c.trigger)):
            return HandInput(False, None, 0.0, 0.0, f"grip/trigger out of range ({c.grip!r}, {c.trigger!r})")
        pos, _ = controller_in_robot(c.pose, self.yaw)
        return HandInput(True, pos, min(max(c.grip, 0.0), 1.0), min(max(c.trigger, 0.0), 1.0), t=t_device)


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
    start_action: dict[str, float]     # the start pose the follower aligned to (degrees, lerobot keys)
    cfg: TeleopConfig = field(default_factory=TeleopConfig)

    def __post_init__(self):
        self.q = deg_dict_to_q(self.start_action)
        if not self.kin.within_limits(self.q, tol=1e-6):
            raise ValueError(f"{self.side}: start pose outside joint limits (with margin)")
        self.gripper = float(self.start_action["gripper.pos"] if "gripper.pos" in self.start_action
                             else self.start_action["gripper"])
        self.state, self.armed = HOLD, False
        self.anchor_hand: np.ndarray | None = None
        self.anchor_tcp: np.ndarray | None = None
        self.anchor_q: np.ndarray | None = None
        self.p_target: np.ndarray | None = None
        self.q_ik = self.q.copy()          # last IK answer; self.q (sent) trails it by the rate limit
        self.grip_synced = False
        self.squeeze_steps = 0             # consecutive valid squeezed steps while HOLD (engage delay)
        self.squeeze_t0: float | None = None  # device time the current squeeze started
        self.last_hand: tuple[float, np.ndarray] | None = None  # (device t, pos) for the speed check
        self.dt = 1.0 / self.cfg.hz

    # ------------------------------------------------------------------------------------
    def action(self) -> dict[str, float]:
        return q_to_action(self.q, self.gripper)

    def _out(self, event=None, err=0.0, sigma=math.nan) -> StepOut:
        return StepOut(self.action(), self.state, event, err, sigma)

    def _fault(self, why: str) -> StepOut:
        self.state = FAULT
        self.q_ik = self.q.copy()
        return self._out(f"FAULT: {why} (targets frozen; release grip, then squeeze to re-anchor)")

    def reset_to(self, action: dict[str, float]) -> None:
        """Make `action` (the last packet actually sent) the current target again."""
        self.q = deg_dict_to_q(action)
        self.q_ik = self.q.copy()
        self.gripper = float(action["gripper.pos"])

    def move_toward(self, target: dict[str, float], max_dps: float) -> bool:
        """Joint-space move of the HELD target toward `target` (record 'a' key: soft_move_to).

        Only while not ENGAGED; rate-limited like teleop; True when arrived (all within 0.5 deg).
        """
        if self.state == ENGAGED:
            return False
        q_t = self.kin.clamp(deg_dict_to_q(target))
        g_t = float(target.get("gripper.pos", target.get("gripper", self.gripper)))
        lim = math.radians(max_dps) * self.dt
        self.q = self.q + np.clip(q_t - self.q, -lim, lim)
        self.q_ik = self.q.copy()
        self.gripper += float(np.clip(g_t - self.gripper, -max_dps * self.dt, max_dps * self.dt))
        return bool(np.max(np.abs(np.rad2deg(q_t - self.q))) < 0.5 and abs(g_t - self.gripper) < 0.5)

    def reset_squeeze(self) -> None:
        self.squeeze_steps, self.squeeze_t0 = 0, None

    def clear_fault_on_release(self, h: HandInput) -> str | None:
        """FAULT -> HOLD on a valid open grip even when following is not allowed (e.g. link down).
        Safe: HOLD sends the same target as FAULT."""
        if self.state == FAULT and h.valid and h.clutch < self.cfg.clutch_off:
            self.state, self.armed = HOLD, True
            return "fault cleared by grip release (HOLD)"
        return None

    def force_fault(self, why: str) -> StepOut:
        """For faults found outside this class (validator, link)."""
        return self._fault(why) if self.state == ENGAGED else self._out()

    def step(self, h: HandInput) -> StepOut:
        cfg = self.cfg
        released = h.valid and h.clutch < cfg.clutch_off   # only a VALID sample can count as a release
        if released:
            self.armed = True

        if self.state == FAULT:
            if released:
                self.state = HOLD
                return self._out("fault cleared by grip release (HOLD)")
            return self._out()
        if self.state == ENGAGED and not h.valid:
            return self._fault(h.why_invalid or "input invalid")
        if self.state == ENGAGED and released:
            self.state = HOLD
            return self._out("released: HOLD at the last sent target")

        if self.state == HOLD:
            if not (h.valid and self.armed and h.clutch >= cfg.clutch_on):
                self.reset_squeeze()
                return self._out()
            # Hold time on the DEVICE clock when known (a stalled sample must not count), else ticks.
            self.squeeze_steps += 1
            if h.t is not None and self.squeeze_t0 is None:
                self.squeeze_t0 = h.t
            held = (h.t - self.squeeze_t0) if h.t is not None else self.squeeze_steps * self.dt
            if held < cfg.engage_hold_s - 1e-9:
                return self._out()  # keep holding the grip: brief accidental squeezes do nothing
            self.reset_squeeze()
            self.last_hand = (h.t, h.pos.copy()) if h.t is not None else None
            self.anchor_hand = h.pos.copy()
            self.anchor_tcp = self.kin.fk(self.q).translation.copy()
            self.anchor_q = self.q.copy()
            self.p_target = self.anchor_tcp.copy()
            self.q_ik = self.q.copy()
            self.state, self.grip_synced = ENGAGED, False
            return self._out("ENGAGED: anchored at the current target (no jump)")

        # ENGAGED and valid: input sanity (a hidden controller "catches up" in one jump, log-008)
        if h.t is not None:
            if self.last_hand is not None and h.t > self.last_hand[0]:
                step_m = float(np.linalg.norm(h.pos - self.last_hand[1]))
                if step_m > cfg.max_hand_step_m:
                    return self._fault(f"hand jumped {step_m * 100:.0f} cm in one update "
                                       f"(> {cfg.max_hand_step_m * 100:.0f}): tracking catch-up? keep it in view")
                speed = step_m / (h.t - self.last_hand[0])
                if speed > cfg.max_hand_speed_mps:
                    return self._fault(f"hand jumped at {speed:.1f} m/s (> {cfg.max_hand_speed_mps}): "
                                       "tracking catch-up? keep the controller in view")
            if self.last_hand is None or h.t > self.last_hand[0]:
                self.last_hand = (h.t, h.pos.copy())
        p_des_check = self.anchor_tcp + cfg.scale * (h.pos - self.anchor_hand)
        lag_m = float(np.linalg.norm(p_des_check - self.p_target))
        if lag_m > cfg.max_target_lag_m:
            return self._fault(f"hand is {lag_m * 100:.0f} cm ahead of the arm target "
                               f"(> {cfg.max_target_lag_m * 100:.0f}): move slower")
        # follow
        err, sigma = 0.0, math.nan
        lag = float(np.max(np.abs(np.rad2deg(self.q_ik - self.q))))
        if lag <= cfg.max_lag_deg:  # otherwise the target waits while the joints catch up
            p_des = self.anchor_tcp + cfg.scale * (h.pos - self.anchor_hand)
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
                return self._fault(f"IK jumped {jump:.1f} deg in one cycle (> {cfg.max_ik_jump_deg})")
            posture = np.abs(np.rad2deg(res.q - self.anchor_q))
            if posture.max() > cfg.max_posture_change_deg:
                j = int(posture.argmax()) + 1
                return self._fault(f"joint_{j} would move {posture.max():.0f} deg from where you engaged "
                                   f"(> {cfg.max_posture_change_deg:.0f}): large posture change")
            self.p_target, self.q_ik, err, sigma = p_tgt, res.q, res.err_m, res.sigma_min
        qlim = math.radians(cfg.max_joint_speed_dps) * self.dt
        self.q = self.kin.clamp(self.q + np.clip(self.q_ik - self.q, -qlim, qlim))

        g_des = cfg.gripper_open_deg + h.gripper * (cfg.gripper_closed_deg - cfg.gripper_open_deg)
        # Soft takeover: the gripper stays put until the trigger's mapped value comes within 3 deg of
        # the gripper's current angle (re-armed at every engage), then follows at <= 120 deg/s.
        if not self.grip_synced and abs(g_des - self.gripper) <= 3.0:
            self.grip_synced = True
        if self.grip_synced:
            glim = cfg.max_gripper_speed_dps * self.dt
            self.gripper += float(np.clip(g_des - self.gripper, -glim, glim))
        return self._out(None, err, sigma)
