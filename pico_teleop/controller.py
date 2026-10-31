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
import pinocchio as pin

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
    gripper_open_deg: float = -58.0    # trigger released. Measured 2026-10-31: left gripper opened by
                                       # hand to its stop reads -60.1 deg (logical, offset 0) -> 2 deg short
    gripper_closed_deg: float = 0.0    # trigger fully pulled
    max_tcp_speed_mps: float = 0.25    # TCP target rate limit
    max_joint_speed_dps: float = 45.0  # joint rate limit applied before validation (validator: 60)
    max_gripper_speed_dps: float = 120.0
    gripper_sync_dps: float = 30.0     # "gentle sync": speed toward the trigger until they match
                                       # (log-012; was: no motion until a near-full trigger pull)
    ik_solve_tol_m: float = 1e-4       # IK iterates to this every cycle (smooth, no 5 mm staircase)
    ik_tol_m: float = 0.005            # accepted if the answer is within 5 mm, else "unreachable"
    ik_max_iter: int = 60
    min_sigma: float = 0.03            # smallest singular value of the 3x7 position Jacobian
    max_ik_jump_deg: float = 5.0       # IK answer vs previous IK answer per cycle (TCP moves <= 5 mm)
    max_posture_change_deg: float = 60.0  # any joint this far from its value at engage -> FAULT
                                          # (45 -> 60 on 2026-10-31, Suraj: 45 tripped twice in normal
                                          # large moves in arm session 2, log-010/011)
    limit_band_deg: float = 15.0       # joints closer than this to a limit are pushed away (null space)
    null_gain: float = 0.0             # OFF (log-004: it moved the arm at engage); rad per IK step
    max_lag_deg: float = 10.0          # sent joints may trail the IK answer by this much; beyond it
                                       # the TCP target waits for the joints to catch up
    operator_yaw_deg: float = 0.0      # operator facing direction relative to robot +x
    # --- wrist rotation (log-015). OFF by default until validated in a dry run and on the arm.
    orientation: bool = False          # True: the hand's rotation (since engage) also turns the gripper
    max_rot_speed_dps: float = 90.0    # rate limit of the gripper's target rotation
    ik_rot_tol_deg: float = 3.0        # accepted orientation error, else FAULT (unreachable)
    ik_rot_weight: float = 0.3         # IK weight: 1 rad of orientation error ~ 0.3 m of position
    max_hand_rot_step_deg: float = 30.0  # hand rotated this much in ONE device update -> FAULT
    max_hand_rot_speed_dps: float = 720.0  # hand rotation speed on the device clock -> FAULT. Live data
                                           # (log-015): normal p99 <= 220, max 470 deg/s; catch-ups 1732-4466
    max_rot_lag_deg: float = 40.0      # gripper target trailing the hand's asked rotation -> FAULT
    rot_fault_deg: float = 20.0        # orientation off by more than this after IK -> FAULT; below it the
                                       # gripper follows the position with the best reachable rotation
    # --- session guards (log-017, after arm session 4: 58 FAULTs, re-engage loops, arm crept to limits)
    fault_lock_count: int = 2          # the same FAULT this many times ...
    fault_lock_window_s: float = 30.0  # ... within this window -> engaging LOCKED until the sender restarts
    max_session_drift_deg: float = 60.0  # no joint may get further than this from the session's start pose
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
    rot: np.ndarray | None = None  # hand rotation in the ROBOT frame (3x3); used with orientation


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
        pos, rot = controller_in_robot(c.pose, self.yaw)
        return HandInput(True, pos, min(max(c.grip, 0.0), 1.0), min(max(c.trigger, 0.0), 1.0), t=t_device,
                         rot=rot)


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
        self.q_session = self.q.copy()     # start pose of this sender session (drift limit)
        self.n_steps = 0                   # step() calls; x dt = session time for the FAULT window
        self.fault_times: dict[str, list[float]] = {}
        self.locked: str | None = None     # reason engaging is locked (cleared only by a restart)
        self.squeeze_steps = 0             # consecutive valid squeezed steps while HOLD (engage delay)
        self.squeeze_t0: float | None = None  # device time the current squeeze started
        self.last_hand: tuple[float, np.ndarray] | None = None  # (device t, pos) for the speed check
        self.last_hand_rot: np.ndarray | None = None
        self.last_rot_t: float | None = None
        self.anchor_hand_rot: np.ndarray | None = None
        self.anchor_tcp_rot: np.ndarray | None = None
        self.R_target: np.ndarray | None = None       # rate-limited gripper orientation target
        self.dt = 1.0 / self.cfg.hz

    # ------------------------------------------------------------------------------------
    def action(self) -> dict[str, float]:
        return q_to_action(self.q, self.gripper)

    def _out(self, event=None, err=0.0, sigma=math.nan) -> StepOut:
        return StepOut(self.action(), self.state, event, err, sigma)

    def _fault(self, why: str) -> StepOut:
        self.state = FAULT
        self.q_ik = self.q.copy()
        key = why.split("(")[0].split(":")[0].strip()[:30]
        now = self.n_steps * self.dt
        times = [t for t in self.fault_times.get(key, []) if now - t <= self.cfg.fault_lock_window_s] + [now]
        self.fault_times[key] = times
        if len(times) >= self.cfg.fault_lock_count and self.locked is None:
            self.locked = (f"the same FAULT ({key}) {len(times)}x within {self.cfg.fault_lock_window_s:.0f} s: "
                           "engaging LOCKED. Stop: esc the follower, restart follower and sender (R2)")
            return self._out(f"FAULT: {why} | LOCKED: {self.locked}")
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
        self.n_steps += 1
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
            if self.locked:
                self.reset_squeeze()
                return self._out("engage refused: " + self.locked)
            drift = float(np.max(np.abs(np.rad2deg(self.q - self.q_session))))
            if drift > cfg.max_session_drift_deg:
                self.reset_squeeze()
                return self._out(f"engage refused: the arm is {drift:.0f} deg from the session start pose "
                                 f"(> {cfg.max_session_drift_deg:.0f}): end with esc and restart (R2)")
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
            M0 = self.kin.fk(self.q)
            self.anchor_tcp = M0.translation.copy()
            self.anchor_tcp_rot = M0.rotation.copy()
            self.R_target = M0.rotation.copy()
            self.anchor_hand_rot = h.rot.copy() if h.rot is not None else None
            self.last_hand_rot, self.last_rot_t = self.anchor_hand_rot, h.t
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
        if cfg.orientation and h.rot is not None and self.last_hand_rot is not None:
            step = math.degrees(float(np.linalg.norm(pin.log3(h.rot @ self.last_hand_rot.T))))
            if step > cfg.max_hand_rot_step_deg:
                return self._fault(f"hand rotation jumped {step:.0f} deg in one update "
                                   f"(> {cfg.max_hand_rot_step_deg:.0f}): tracking catch-up? keep it in view")
            dt_rot = (h.t - self.last_rot_t) if (h.t is not None and self.last_rot_t is not None) else None
            if dt_rot is not None and dt_rot > 0 and step / dt_rot > cfg.max_hand_rot_speed_dps:
                return self._fault(f"hand rotation jumped at {step / dt_rot:.0f} deg/s "
                                   f"(> {cfg.max_hand_rot_speed_dps:.0f}): tracking catch-up? keep it in view")
            if h.t is None or self.last_rot_t is None or h.t > self.last_rot_t:
                self.last_hand_rot, self.last_rot_t = h.rot.copy(), h.t
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
            R_tgt = None
            if cfg.orientation and h.rot is not None and self.anchor_hand_rot is not None:
                # desired = (hand rotation since engage, robot frame) applied to the gripper's anchor
                R_des = (h.rot @ self.anchor_hand_rot.T) @ self.anchor_tcp_rot
                w = pin.log3(R_des @ self.R_target.T)
                ang, lim_r = float(np.linalg.norm(w)), math.radians(cfg.max_rot_speed_dps) * self.dt
                if math.degrees(ang) > cfg.max_rot_lag_deg:  # also keeps log3 far from its pi flip
                    return self._fault(f"hand turned {math.degrees(ang):.0f} deg ahead of the gripper "
                                       f"(> {cfg.max_rot_lag_deg:.0f}): turn slower")
                R_tgt = pin.exp3(w * (lim_r / ang) if ang > lim_r else w) @ self.R_target
                res, rot_err = self.kin.solve_pose(p_tgt, R_tgt, self.q_ik, tol_m=cfg.ik_solve_tol_m,
                                                   max_iter=cfg.ik_max_iter, w_rot=cfg.ik_rot_weight)
                if res.err_m > cfg.ik_tol_m:
                    # Position first: if position + rotation together are not reachable, follow the
                    # position with the rotation the arm can do (orientation free, seeded here).
                    res = self.kin.solve_position(p_tgt, self.q_ik, tol_m=cfg.ik_solve_tol_m,
                                                  max_iter=cfg.ik_max_iter)
                    R_got = self.kin.fk(res.q).rotation
                    rot_err = float(np.linalg.norm(pin.log3(R_got @ R_tgt.T)))
                if math.degrees(rot_err) > cfg.rot_fault_deg:
                    return self._fault(f"wrist cannot reach that rotation ({math.degrees(rot_err):.0f} deg off): "
                                       "turn the hand back")
                if math.degrees(rot_err) > cfg.ik_rot_tol_deg:
                    R_tgt = self.kin.fk(res.q).rotation  # best reachable rotation becomes the target
            else:
                res = self.kin.solve_position(p_tgt, self.q_ik, tol_m=cfg.ik_solve_tol_m, max_iter=cfg.ik_max_iter,
                                             limit_band_deg=cfg.limit_band_deg, null_gain=cfg.null_gain)
            if res.err_m > cfg.ik_tol_m:
                return self._fault(f"IK did not converge ({res.err_m * 1000:.1f} mm): target unreachable")
            if res.sigma_min < cfg.min_sigma:
                return self._fault(f"near singular (sigma {res.sigma_min:.3f} < {cfg.min_sigma})")
            jump = float(np.max(np.abs(np.rad2deg(res.q - self.q_ik))))
            if jump > cfg.max_ik_jump_deg:
                return self._fault(f"IK jumped {jump:.1f} deg in one cycle (> {cfg.max_ik_jump_deg})")
            drift = np.abs(np.rad2deg(res.q - self.q_session))
            if drift.max() > cfg.max_session_drift_deg:
                j = int(drift.argmax()) + 1
                return self._fault(f"joint_{j} would be {drift.max():.0f} deg from the session start pose "
                                   f"(> {cfg.max_session_drift_deg:.0f}): bring the hand back")
            posture = np.abs(np.rad2deg(res.q - self.anchor_q))
            if posture.max() > cfg.max_posture_change_deg:
                j = int(posture.argmax()) + 1
                return self._fault(f"joint_{j} would move {posture.max():.0f} deg from where you engaged "
                                   f"(> {cfg.max_posture_change_deg:.0f}): large posture change")
            self.p_target, self.q_ik, err, sigma = p_tgt, res.q, res.err_m, res.sigma_min
            if R_tgt is not None:
                self.R_target = R_tgt
        qlim = math.radians(cfg.max_joint_speed_dps) * self.dt
        self.q = self.kin.clamp(self.q + np.clip(self.q_ik - self.q, -qlim, qlim))

        g_des = cfg.gripper_open_deg + h.gripper * (cfg.gripper_closed_deg - cfg.gripper_open_deg)
        # Gentle sync: after each engage the gripper moves toward the trigger's mapped angle at only
        # gripper_sync_dps (30 deg/s: a closed gripper with the trigger released opens over ~2 s,
        # visible and stoppable by releasing the grip); once within 3 deg it follows at <= 120 deg/s.
        if not self.grip_synced and abs(g_des - self.gripper) <= 3.0:
            self.grip_synced = True
        speed = cfg.max_gripper_speed_dps if self.grip_synced else cfg.gripper_sync_dps
        glim = speed * self.dt
        self.gripper += float(np.clip(g_des - self.gripper, -glim, glim))
        return self._out(None, err, sigma)
