"""Wrist rotation (log-015): the hand's rotation since engage turns the gripper; limits and FAULTs."""
import math

import numpy as np
import pinocchio as pin
import pytest

from pico_teleop.controller import ENGAGED, FAULT, ArmTeleop, PicoAdapter, TeleopConfig
from pico_teleop.frames import R_ROBOT_FROM_XR
from pico_teleop.kinematics import ArmKinematics, deg_dict_to_q
from pico_teleop.poses import load_start_pose
from pico_teleop.validate import FOLLOWER_LIMITS_DEG, ActionValidator
from pico_teleop.xr_input import ControllerSample

XR0 = np.array([0.2, 1.0, -0.4])
ADAPT = PicoAdapter()
_n = [0]


def _quat_from_robot_rot(R_robot):
    """Controller quaternion (OpenXR) whose robot-frame rotation is R_robot."""
    M = R_ROBOT_FROM_XR.T @ R_robot @ R_ROBOT_FROM_XR
    q = pin.Quaternion(M)
    return (q.x, q.y, q.z, q.w)


def _c(axis=(0, 0, 1), deg=0.0, delta=(0, 0, 0), grip=1.0):
    _n[0] += 1
    R = pin.exp3(np.asarray(axis, float) * math.radians(deg))
    p = XR0 + R_ROBOT_FROM_XR.T @ (np.asarray(delta, float) + 1e-6 * (_n[0] % 5))
    return ControllerSample(tuple(p) + _quat_from_robot_rot(R), grip, 1.0)


def _arm(side="left", **cfg):
    cfg.setdefault("engage_hold_s", 0.0)
    cfg.setdefault("orientation", True)
    arm = ArmTeleop(side, ArmKinematics(side, limits_deg=FOLLOWER_LIMITS_DEG[side], margin_deg=2.0),
                    load_start_pose(side), TeleopConfig(**cfg))
    arm.step(ADAPT(_c(grip=0.0), True))
    assert arm.step(ADAPT(_c(), True)).state == ENGAGED
    return arm


def _tcp_rot_deg(arm):
    return np.degrees(pin.log3(arm.kin.fk(arm.q).rotation @ arm.anchor_tcp_rot.T))


def test_solve_pose_round_trip():
    kin = ArmKinematics("left", limits_deg=FOLLOWER_LIMITS_DEG["left"], margin_deg=2.0)
    q0 = deg_dict_to_q(load_start_pose("left"))
    M = kin.fk(q0)
    R_t = pin.exp3(np.array([0.0, 0.0, math.radians(15)])) @ M.rotation
    res, err_r = kin.solve_pose(M.translation, R_t, q0, max_iter=200)
    assert res.err_m < 1e-3 and math.degrees(err_r) < 0.5


@pytest.mark.parametrize("side", ["left", "right"])
@pytest.mark.parametrize("axis", [(1, 0, 0), (0, 1, 0), (0, 0, 1)])
def test_hand_rotation_turns_the_gripper_the_same_way(side, axis):
    arm = _arm(side)
    p0 = arm.kin.fk(arm.q).translation.copy()
    for i in range(1, 51):  # 20 deg over 1 s (rate limit 90 deg/s)
        out = arm.step(ADAPT(_c(axis, 20.0 * i / 50), True))
        assert out.state == ENGAGED, out.event
    for _ in range(50):
        out = arm.step(ADAPT(_c(axis, 20.0), True))
    w = _tcp_rot_deg(arm)
    assert np.dot(w, axis) == pytest.approx(20.0, abs=1.5), w     # same axis, same angle
    assert np.linalg.norm(w - 20.0 * np.asarray(axis)) < 2.0
    assert np.linalg.norm(arm.kin.fk(arm.q).translation - p0) < 0.006  # position held (5 mm IK tolerance)


def test_orientation_off_ignores_rotation():
    arm = _arm(orientation=False)
    for i in range(50):
        arm.step(ADAPT(_c((0, 0, 1), 20.0), True))
    assert out_ok(arm)


def out_ok(arm):
    return arm.state == ENGAGED and arm.R_target is not None and \
        np.allclose(arm.R_target, arm.anchor_tcp_rot)  # target rotation never moved


def test_rotation_is_rate_limited_and_validated():
    arm = _arm()
    val = ActionValidator("left", arm.cfg.hz)
    prev = arm.action()
    for i in range(30):  # hand snaps 40 deg at once (one update, below the 45 deg jump limit)
        out = arm.step(ADAPT(_c((0, 0, 1), 40.0), True))
        assert val.check(out.action, prev, "left").ok
        prev = out.action
    w = _tcp_rot_deg(arm)
    assert abs(w[2]) <= 90.0 * 30 / 50 + 1.0  # at most 90 deg/s


def test_rotation_jump_faults():
    arm = _arm()
    out = arm.step(ADAPT(_c((0, 0, 1), 60.0), True, "", None))
    assert out.state == FAULT and "rotation jumped" in out.event


def test_unreachable_rotation_faults():
    arm = _arm()
    events = []
    for i in range(1, 400):  # keep turning about x slowly (75 deg/s < 90 deg/s limit, no lag FAULT)
        out = arm.step(ADAPT(_c((1, 0, 0), min(1.5 * i, 170.0)), True))
        events.append(out.event or "")
        if out.state == FAULT:
            break
    assert out.state == FAULT and any(k in out.event for k in ("rotation", "IK jumped", "posture", "singular"))


def _c_rot(R_robot, delta=(0, 0, 0), grip=1.0):
    _n[0] += 1
    p = XR0 + R_ROBOT_FROM_XR.T @ (np.asarray(delta, float) + 1e-6 * (_n[0] % 5))
    return ControllerSample(tuple(p) + _quat_from_robot_rot(R_robot), grip, 1.0)


def _arm_engaged_at(R0, side="left", **cfg):
    cfg.setdefault("engage_hold_s", 0.0)
    cfg.setdefault("orientation", True)
    arm = ArmTeleop(side, ArmKinematics(side, limits_deg=FOLLOWER_LIMITS_DEG[side], margin_deg=2.0),
                    load_start_pose(side), TeleopConfig(**cfg))
    arm.step(ADAPT(_c_rot(R0, grip=0.0), True))
    assert arm.step(ADAPT(_c_rot(R0), True)).state == ENGAGED
    return arm


@pytest.mark.parametrize("side", ["left", "right"])
def test_tilted_anchor_with_translation(side):
    """Engaged with the hand already turned 90 deg about z; then +20 deg about x while moving 5 cm.
    The gripper must turn +20 deg about robot x (a world axis), whatever the grip angle at engage."""
    R0 = pin.exp3(np.array([0.0, 0.0, math.radians(90)]))
    arm = _arm_engaged_at(R0, side)
    for i in range(1, 51):
        u = i / 50
        R = pin.exp3(np.array([math.radians(20 * u), 0.0, 0.0])) @ R0
        out = arm.step(ADAPT(_c_rot(R, delta=(0.05 * u, 0, 0)), True))
        assert out.state == ENGAGED, out.event
    for _ in range(40):
        arm.step(ADAPT(_c_rot(R, delta=(0.05, 0, 0)), True))
    w = _tcp_rot_deg(arm)
    assert np.linalg.norm(w - np.array([20.0, 0.0, 0.0])) < 2.0, w


def test_rotation_speed_fault_on_device_clock():
    arm = _arm()
    t = 1.0
    for i in range(5):
        t += 0.011
        arm.step(ADAPT(_c((0, 0, 1), 0.5 * i), True, "", t))
    out = arm.step(ADAPT(_c((0, 0, 1), 2.0 + 25.0), True, "", t + 0.011))  # 25 deg in 11 ms = 2270 deg/s
    assert out.state == FAULT and "deg/s" in out.event


def test_rotation_lag_fault():
    arm = _arm()
    out = None
    for i in range(1, 40):  # 28 deg per update would trip the step check; 25 deg per update, no t
        out = arm.step(ADAPT(_c((0, 0, 1), min(25.0 * i, 100.0)), True))
        if out.state == FAULT:
            break
    assert out.state == FAULT and "ahead of the gripper" in out.event


def test_reengage_while_rotated_has_no_jump():
    arm = _arm()
    for i in range(1, 41):
        arm.step(ADAPT(_c((0, 1, 0), 0.5 * i), True))
    arm.step(ADAPT(_c((0, 1, 0), 20.0, grip=0.0), True))  # release while turned
    held = arm.action()
    out = arm.step(ADAPT(_c((0, 1, 0), 35.0), True))       # re-engage with the hand turned further
    assert out.state == ENGAGED and out.action == held
    out = arm.step(ADAPT(_c((0, 1, 0), 35.0), True))
    assert max(abs(out.action[k] - held[k]) for k in held) < 0.05
