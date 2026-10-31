"""Gate 2: FK/IK round trips, unreachable and singular cases, limits, clutch transitions, continuity."""
import math

import numpy as np
import pytest

from pico_teleop.controller import ENGAGED, FAULT, HOLD, ArmTeleop, TeleopConfig
from pico_teleop.frames import R_ROBOT_FROM_XR, controller_in_robot
from pico_teleop.kinematics import ArmKinematics, deg_dict_to_q
from pico_teleop.validate import FOLLOWER_LIMITS_DEG, ActionValidator
from pico_teleop.xr_input import ControllerSample

# takeei-v2 / kitakyushu-takeei-v2 start poses (openarm-orboh config/pose_sets/*/{left,right}.yaml)
START = {
    "left": {"joint_1": 54.43, "joint_2": -1.21, "joint_3": -1.58, "joint_4": 116.91, "joint_5": 24.12,
             "joint_6": -8.09, "joint_7": -46.50, "gripper": 0.0},
    "right": {"joint_1": -53.07, "joint_2": -0.68, "joint_3": 0.34, "joint_4": 132.21, "joint_5": -23.63,
              "joint_6": 12.61, "joint_7": 8.45, "gripper": 0.0},
}
ID = (0.0, 0.0, 0.0, 1.0)
XR0 = np.array([0.2, 1.0, -0.4])  # a plausible controller position in OpenXR


@pytest.fixture(scope="module", params=["left", "right"])
def kin(request):
    s = request.param
    return ArmKinematics(s, limits_deg=FOLLOWER_LIMITS_DEG[s], margin_deg=2.0)


def _xr(robot_delta):
    """Controller pose whose ROBOT-frame offset from XR0 is robot_delta."""
    p = XR0 + R_ROBOT_FROM_XR.T @ np.asarray(robot_delta, float)
    return tuple(p) + ID


def _c(delta=(0, 0, 0), grip=1.0, trig=0.0, pose=None):
    return ControllerSample(pose if pose is not None else _xr(delta), grip, trig)


# ---- frames -------------------------------------------------------------------------------
def test_xr_axes_map_to_robot_axes():
    base, _ = controller_in_robot(tuple(XR0) + ID)
    fwd, _ = controller_in_robot(tuple(XR0 + [0, 0, -0.1]) + ID)   # XR forward = -z
    left, _ = controller_in_robot(tuple(XR0 + [-0.1, 0, 0]) + ID)  # XR left = -x
    up, _ = controller_in_robot(tuple(XR0 + [0, 0.1, 0]) + ID)
    assert np.allclose(fwd - base, [0.1, 0, 0]) and np.allclose(left - base, [0, 0.1, 0])
    assert np.allclose(up - base, [0, 0, 0.1])


# ---- kinematics ---------------------------------------------------------------------------
def test_right_arm_is_its_own_chain():
    l = ArmKinematics("left"); r = ArmKinematics("right")
    assert l.model.names[7] != r.model.names[7]
    q = np.deg2rad([10, -20, 15, 60, 30, 20, 40])
    pl, pr = l.fk(q).translation, r.fk(-q).translation
    assert not np.allclose(pl * [1, -1, 1], pr, atol=1e-3)  # a mirror model would make these equal


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
    assert np.mean(np.array(errs) < 1e-3) > 0.95, f"converged {np.mean(np.array(errs) < 1e-3):.2f}"


def test_unreachable_target_not_converged(kin):
    q0 = deg_dict_to_q(START[kin.side])
    res = kin.solve_position(kin.fk(q0).translation + [1.5, 0, 0], q0)
    assert not res.converged and res.err_m > 0.5
    assert kin.within_limits(res.q)


def test_straight_arm_is_singular(kin):
    assert kin.sigma_min(np.zeros(7)) < 1e-6
    assert kin.sigma_min(deg_dict_to_q(START[kin.side])) > 0.1


# ---- clutch state machine -----------------------------------------------------------------
def _arm(side, **cfg):
    s = side
    return ArmTeleop(s, ArmKinematics(s, limits_deg=FOLLOWER_LIMITS_DEG[s], margin_deg=2.0),
                     {f"{k}.pos": v for k, v in START[s].items()}, TeleopConfig(**cfg))


@pytest.mark.parametrize("side", ["left", "right"])
def test_engage_has_no_jump_and_release_holds(side):
    arm = _arm(side)
    a0 = arm.action()
    assert arm.step(_c(grip=0.0), True).state == HOLD  # armed by a release
    out = arm.step(_c(grip=1.0), True)
    assert out.state == ENGAGED and out.action == a0  # first engaged command == start pose exactly
    for i in range(50):
        out = arm.step(_c((0.002 * i, 0, 0)), True)
    moved = out.action
    assert moved["joint_1.pos"] != a0["joint_1.pos"]
    out = arm.step(_c((0.3, 0, 0), grip=0.0), True)
    assert out.state == HOLD and out.action == moved  # release keeps the last target
    for _ in range(20):  # moving while released does nothing
        assert arm.step(_c((0.5, 0.2, 0), grip=0.0), True).action == moved


def test_reengage_reanchors():
    arm = _arm("right")
    arm.step(_c(grip=0.0), True); arm.step(_c(grip=1.0), True)
    for i in range(30):
        arm.step(_c((0.002 * i, 0, 0)), True)
    arm.step(_c((0.06, 0, 0), grip=0.0), True)
    held = arm.action()
    out = arm.step(_c((-0.2, 0.1, 0.0), grip=1.0), True)  # hand elsewhere: re-anchor, no jump
    assert out.state == ENGAGED and out.action == held
    out = arm.step(_c((-0.2, 0.1, 0.0)), True)
    assert max(abs(out.action[k] - held[k]) for k in held) < 1e-6


def test_grip_held_at_start_does_not_engage():
    arm = _arm("left")
    for _ in range(10):
        assert arm.step(_c(grip=1.0), True).state == HOLD
    arm.step(_c(grip=0.0), True)
    assert arm.step(_c(grip=1.0), True).state == ENGAGED


@pytest.mark.parametrize("bad", ["stale", "zero", "nan"])
def test_input_loss_faults_and_needs_release(bad):
    arm = _arm("right")
    arm.step(_c(grip=0.0), True); arm.step(_c(grip=1.0), True)
    for i in range(10):
        arm.step(_c((0.002 * i, 0, 0)), True)
    held = arm.action()
    c = {"stale": _c((0.05, 0, 0)), "zero": _c(pose=(0.0,) * 7),
         "nan": _c(pose=(math.nan,) * 3 + ID)}[bad]
    out = arm.step(c, bad != "stale")
    assert out.state == FAULT and out.action == held and "FAULT" in out.event
    assert arm.step(_c((0.1, 0, 0)), True).state == FAULT  # recovered input does not resume
    assert arm.step(_c((0.1, 0, 0), grip=0.0), True).state == HOLD
    assert arm.step(_c((0.1, 0, 0), grip=1.0), True).action == held  # re-anchored, no jump


def test_unreachable_faults():
    arm = _arm("left", scale=1.0, max_tcp_speed_mps=100.0)
    arm.step(_c(grip=0.0), True); arm.step(_c(grip=1.0), True)
    held = arm.action()
    out = arm.step(_c((2.0, 0, 0)), True)
    assert out.state == FAULT and "unreachable" in out.event and out.action == held


@pytest.mark.parametrize("side", ["left", "right"])
@pytest.mark.parametrize("shape", ["circle", "line", "fast_line"])
def test_trajectory_continuity_and_validation(side, shape):
    """Synthetic hand trajectories: every output passes the sender validator, no faults."""
    arm = _arm(side)
    val = ActionValidator(side, arm.cfg.hz)
    arm.step(_c(grip=0.0), True)
    prev = arm.step(_c(grip=1.0), True).action
    n = 400
    for i in range(n):
        t = i / arm.cfg.hz
        if shape == "circle":
            d = (0.08 * math.cos(t) - 0.08, 0.08 * math.sin(t), 0.0)
        elif shape == "line":
            d = (0.0, 0.0, -0.2 * i / n)
        else:  # hand jumps 30 cm at once: rate limits must spread it
            d = (0.0, -0.3, 0.0) if i > 5 else (0, 0, 0)
        out = arm.step(_c(d, trig=1.0 - i / n), True)  # starts fully pulled = closed (takeover)
        assert out.state == ENGAGED, out.event
        v = val.check(out.action, prev, side)
        assert v.ok, v.reasons
        prev = out.action
    assert prev["gripper.pos"] < -50.0  # trigger released over the run: gripper opened


def test_gripper_soft_takeover():
    arm = _arm("right")
    arm.step(_c(grip=0.0), True); arm.step(_c(grip=1.0), True)
    for _ in range(20):  # trigger released (= open) while the gripper is closed: no motion
        assert arm.step(_c(trig=0.0), True).action["gripper.pos"] == 0.0
    arm.step(_c(trig=1.0), True)  # pulled fully = closed = current value: takes over
    for i in range(60):
        out = arm.step(_c(trig=0.5), True)
    assert out.action["gripper.pos"] == pytest.approx(-30.0, abs=0.5)
