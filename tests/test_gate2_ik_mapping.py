"""Gate 2: FK/IK round trips, unreachable and singular cases, limits, clutch transitions, continuity.
Start poses come from the pose set in force (openarm-orboh config/pose_sets/CURRENT)."""
import math

import numpy as np
import pytest

from pico_teleop.controller import ENGAGED, FAULT, HOLD, ArmTeleop, PicoAdapter, TeleopConfig
from pico_teleop.frames import R_ROBOT_FROM_XR, controller_in_robot
from pico_teleop.kinematics import ArmKinematics, deg_dict_to_q
from pico_teleop.poses import current_pose_set, load_start_pose
from pico_teleop.validate import FOLLOWER_LIMITS_DEG, ActionValidator
from pico_teleop.xr_input import ControllerSample

POSE_SET = current_pose_set()
START = {s: load_start_pose(s, POSE_SET) for s in ("left", "right")}
ID = (0.0, 0.0, 0.0, 1.0)
XR0 = np.array([0.2, 1.0, -0.4])
ADAPT = PicoAdapter()
_n = [0]


def _xr(robot_delta):
    _n[0] += 1  # tracking noise: real poses are never bit-identical
    p = XR0 + R_ROBOT_FROM_XR.T @ (np.asarray(robot_delta, float) + 1e-6 * (_n[0] % 5))
    return tuple(p) + ID


def _c(delta=(0, 0, 0), grip=1.0, trig=1.0, pose=None):
    return ControllerSample(pose if pose is not None else _xr(delta), grip, trig)


def _h(c, fresh=True):
    return ADAPT(c, fresh)


@pytest.fixture(scope="module", params=["left", "right"])
def kin(request):
    s = request.param
    return ArmKinematics(s, limits_deg=FOLLOWER_LIMITS_DEG[s], margin_deg=2.0)


def _arm(side, **cfg):
    cfg.setdefault("engage_hold_s", 0.0)  # most tests check other things; the hold has its own test
    return ArmTeleop(side, ArmKinematics(side, limits_deg=FOLLOWER_LIMITS_DEG[side], margin_deg=2.0),
                     START[side], TeleopConfig(**cfg))


def _engaged(side, **cfg):
    arm = _arm(side, **cfg)
    arm.step(_h(_c(grip=0.0)))
    assert arm.step(_h(_c())).state == ENGAGED
    return arm


# ---- frames and kinematics ----------------------------------------------------------------
def test_xr_axes_map_to_robot_axes():
    base, _ = controller_in_robot(tuple(XR0) + ID)
    fwd, _ = controller_in_robot(tuple(XR0 + [0, 0, -0.1]) + ID)   # XR forward = -z
    left, _ = controller_in_robot(tuple(XR0 + [-0.1, 0, 0]) + ID)  # XR left = -x
    up, _ = controller_in_robot(tuple(XR0 + [0, 0.1, 0]) + ID)
    assert np.allclose(fwd - base, [0.1, 0, 0]) and np.allclose(left - base, [0, 0.1, 0])
    assert np.allclose(up - base, [0, 0, 0.1])


def test_right_arm_is_its_own_chain():
    l, r = ArmKinematics("left"), ArmKinematics("right")
    q = np.deg2rad([10, -20, 15, 60, 30, 20, 40])
    assert not np.allclose(l.fk(q).translation * [1, -1, 1], r.fk(-q).translation, atol=1e-3)


def test_tcp_is_beyond_the_wrist(kin):
    """The URDF hand_tcp is ON link7 (zero offset); ours is 0.1025 m out, so joints 5-7 move it."""
    q = deg_dict_to_q(START[kin.side])
    assert np.all(np.linalg.norm(kin.jac_pos(q), axis=0)[4:] > 0.01)
    wrist = ArmKinematics(kin.side, tcp_offset_m=0.0)
    assert np.linalg.norm(kin.fk(q).translation - wrist.fk(q).translation) == pytest.approx(0.1025, abs=1e-6)


def test_fk_ik_round_trip_near_seed(kin):
    rng = np.random.default_rng(0)
    errs = []
    for _ in range(100):
        q = kin.lo + (kin.hi - kin.lo) * rng.random(7)
        if kin.sigma_min(q) < 0.03:
            continue
        seed = kin.clamp(q + np.deg2rad(rng.uniform(-10, 10, 7)))
        res = kin.solve_position(kin.fk(q).translation, seed, max_iter=100)
        assert kin.within_limits(res.q)
        errs.append(res.err_m)
    assert np.mean(np.array(errs) < 1e-3) > 0.95


def test_unreachable_target_not_converged(kin):
    q0 = deg_dict_to_q(START[kin.side])
    res = kin.solve_position(kin.fk(q0).translation + [1.5, 0, 0], q0)
    assert not res.converged and res.err_m > 0.5 and kin.within_limits(res.q)


def test_straight_arm_is_singular_start_pose_is_not(kin):
    assert kin.sigma_min(np.zeros(7)) < 1e-6
    assert kin.sigma_min(deg_dict_to_q(START[kin.side])) > 0.1


# ---- clutch ---------------------------------------------------------------------------------
@pytest.mark.parametrize("side", ["left", "right"])
def test_engage_has_no_jump_and_release_holds(side):
    arm = _arm(side)
    a0 = arm.action()
    arm.step(_h(_c(grip=0.0)))
    out = arm.step(_h(_c()))
    assert out.state == ENGAGED and out.action == a0
    for i in range(50):
        out = arm.step(_h(_c((0.002 * i, 0, 0))))
    moved = out.action
    assert moved != a0
    out = arm.step(_h(_c((0.3, 0, 0), grip=0.0)))
    assert out.state == HOLD and out.action == moved
    for _ in range(20):
        assert arm.step(_h(_c((0.5, 0.2, 0), grip=0.0))).action == moved


def test_reengage_reanchors():
    arm = _engaged("right")
    for i in range(30):
        arm.step(_h(_c((0.002 * i, 0, 0))))
    arm.step(_h(_c((0.06, 0, 0), grip=0.0)))
    held = arm.action()
    out = arm.step(_h(_c((-0.2, 0.1, 0.0))))
    assert out.state == ENGAGED and out.action == held
    out = arm.step(_h(_c((-0.2, 0.1, 0.0))))
    assert max(abs(out.action[k] - held[k]) for k in held) < 0.01


def test_grip_held_at_start_does_not_engage():
    arm = _arm("left")
    for _ in range(10):
        assert arm.step(_h(_c())).state == HOLD
    arm.step(_h(_c(grip=0.0)))
    assert arm.step(_h(_c())).state == ENGAGED


def test_zero_frame_does_not_arm_or_clear():
    """Elephant m2 #2: the SDK's all-zero frame reads grip 0; it must not count as a release."""
    arm = _arm("right")
    arm.step(_h(_c(pose=(0.0,) * 7, grip=0.0)))   # startup zero frame
    assert arm.step(_h(_c())).state == HOLD        # grip held: still not armed
    arm = _engaged("right")
    assert arm.step(_h(_c(pose=(0.0,) * 7, grip=0.0))).state == FAULT
    assert arm.step(_h(_c(pose=(0.0,) * 7, grip=0.0))).state == FAULT  # zero frames do not clear it
    assert arm.step(_h(_c())).state == FAULT                            # tracking back, grip held


@pytest.mark.parametrize("bad", ["stale", "zero", "nan", "trigger_nan", "grip_5"])
def test_input_loss_faults_and_needs_release(bad):
    arm = _engaged("right")
    for i in range(10):
        arm.step(_h(_c((0.002 * i, 0, 0))))
    held = arm.action()
    c = {"stale": _c((0.05, 0, 0)), "zero": _c(pose=(0.0,) * 7), "nan": _c(pose=(math.nan,) * 3 + ID),
         "trigger_nan": _c(trig=math.nan), "grip_5": _c(grip=5.0)}[bad]
    out = arm.step(_h(c, bad != "stale"))
    assert out.state == FAULT and out.action == held and "FAULT" in out.event
    assert arm.step(_h(_c((0.1, 0, 0)))).state == FAULT
    assert arm.step(_h(_c((0.1, 0, 0), grip=0.0))).state == HOLD
    assert arm.step(_h(_c((0.1, 0, 0)))).action == held


def test_grip_out_of_range_does_not_engage():
    arm = _arm("left")
    arm.step(_h(_c(grip=0.0)))
    assert arm.step(_h(_c(grip=5.0))).state == HOLD


def test_unreachable_faults():
    arm = _engaged("left", scale=1.0, max_tcp_speed_mps=100.0)
    held = arm.action()
    out = arm.step(_h(_c((2.0, 0, 0))))
    assert out.state == FAULT and out.action == held


def test_posture_change_faults():
    arm = _engaged("right", max_posture_change_deg=10.0)
    events = [arm.step(_h(_c((0.0, 0.004 * i, -0.004 * i)))).event for i in range(200)]
    assert any(e and "posture change" in e for e in events)


def test_gripper_gentle_sync():
    """Engaging with the trigger released: the closed gripper opens SLOWLY (30 deg/s) until it matches,
    then follows the trigger at full speed (120 deg/s)."""
    arm = _engaged("right")
    g = [arm.step(_h(_c(trig=0.0))).action["gripper.pos"] for _ in range(50)]  # 1 s at 50 Hz
    assert g[0] == pytest.approx(-0.6) and g[-1] == pytest.approx(-30.0, abs=0.01)  # 30 deg/s
    assert all(b - a <= 1e-9 for a, b in zip(g, g[1:]))  # only opening, never jumps
    for _ in range(60):
        out = arm.step(_h(_c(trig=0.0)))
    assert out.action["gripper.pos"] == pytest.approx(-58.0) and arm.grip_synced
    seq = [arm.step(_h(_c(trig=1.0))).action["gripper.pos"] for _ in range(5)]
    assert seq[0] == pytest.approx(-55.6)  # synced: 120 deg/s = 2.4 deg per cycle


def test_gripper_resync_after_each_engage():
    arm = _engaged("right")
    for _ in range(150):
        arm.step(_h(_c(trig=0.0)))
    arm.step(_h(_c(trig=0.0, grip=0.0)))   # release
    arm.step(_h(_c(trig=1.0)))             # re-engage with the trigger fully pulled
    assert not arm.grip_synced
    out = arm.step(_h(_c(trig=1.0)))
    assert out.action["gripper.pos"] == pytest.approx(-57.4, abs=0.01)  # one slow 0.6 deg step (30 deg/s)


@pytest.mark.parametrize("side", ["left", "right"])
@pytest.mark.parametrize("shape", ["circle", "line", "fast_line"])
def test_trajectory_continuity_and_validation(side, shape):
    arm = _engaged(side)
    val = ActionValidator(side, arm.cfg.hz)
    prev = arm.action()
    n = 400
    for i in range(n):
        t = i / arm.cfg.hz
        if shape == "circle":
            d = (0.08 * math.cos(t) - 0.08, 0.08 * math.sin(t), 0.0)
        elif shape == "line":
            d = (0.0, 0.0, -0.2 * i / n)
        else:
            d = (0.0, -0.25, 0.0) if i > 5 else (0, 0, 0)  # 0.3 m would trip the 15 cm lag FAULT
        out = arm.step(_h(_c(d, trig=1.0 - i / n)))
        assert out.state == ENGAGED, out.event
        v = val.check(out.action, prev, side)
        assert v.ok, v.reasons
        prev = out.action
    assert prev["gripper.pos"] < -50.0


def test_grip_must_be_held_before_engaging():
    arm = _arm("right", engage_hold_s=0.3)
    arm.step(_h(_c(grip=0.0)))
    for _ in range(2):  # a 40 ms accidental squeeze (trigger pulled the grip) does nothing
        assert arm.step(_h(_c())).state == HOLD
    arm.step(_h(_c(grip=0.0)))
    states = [arm.step(_h(_c())).state for _ in range(16)]
    assert states[13] == HOLD and states[14] == ENGAGED  # 15 steps x 20 ms = 0.3 s


def test_hand_jump_faults():
    """Hidden-controller return in the live data: ~50 cm in one 11-122 ms update (log-008)."""
    arm = _engaged("right")
    t = 0.0
    for i in range(20):
        t += 0.011
        out = arm.step(ADAPT(_c((0.001 * i, 0, 0)), True, "", t))
        assert out.state == ENGAGED
    t += 0.122
    out = arm.step(ADAPT(_c((0.5, 0, 0)), True, "", t))
    assert out.state == FAULT and "hand jumped" in out.event


def test_fast_but_real_hand_motion_does_not_fault():
    arm = _engaged("right")
    t = 0.0
    for i in range(6):  # a short 2.0 m/s flick = the fastest held motion measured live
        t += 0.011
        out = arm.step(ADAPT(_c((0.0, 0.022 * i, 0.0)), True, "", t))
    assert out.state == ENGAGED


def test_target_lag_faults():
    arm = _engaged("right")
    out = arm.step(_h(_c((0.4, 0, 0))))  # desired TCP 20 cm away at once
    assert out.state == FAULT and "ahead of the arm" in out.event


def test_catch_up_after_short_gap_faults_by_step_size():
    """Elephant m5 #3: 52 cm after a 0.2 s gap is only 2.6 m/s, but no hand moves 10 cm in one update."""
    arm = _engaged("right")
    t = 0.0
    for i in range(10):
        t += 0.011
        arm.step(ADAPT(_c((0.001 * i, 0, 0)), True, "", t))
    out = arm.step(ADAPT(_c((0.2, 0, 0)), True, "", t + 0.2))
    assert out.state == FAULT and "in one update" in out.event


def test_grip_hold_uses_device_time():
    """A spike held on a stalled sample must not count as 0.3 s of squeeze."""
    arm = _arm("right", engage_hold_s=0.3)
    arm.step(ADAPT(_c(grip=0.0), True, "", 1.0))
    for _ in range(20):  # 20 sender ticks, but the device clock does not advance
        assert arm.step(ADAPT(_c(), True, "", 1.011)).state == HOLD
    assert arm.step(ADAPT(_c(), True, "", 1.32)).state == ENGAGED
