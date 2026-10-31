"""Sender-side packet validation (the receiver and follower would pass NaN as the upper limit)."""
import math

from pico_teleop.validate import ACTION_KEYS, ActionValidator, looks_like_radians

GOOD = {"joint_1.pos": -53.0, "joint_2.pos": -0.7, "joint_3.pos": 0.3, "joint_4.pos": 132.0,
        "joint_5.pos": -23.6, "joint_6.pos": 12.6, "joint_7.pos": 8.4, "gripper.pos": 0.0}


def v():
    return ActionValidator("right", hz=50.0)


def test_good_action_passes():
    assert v().check(GOOD, None, "right").ok


def test_nan_inf_none_bool_rejected():
    for bad in (math.nan, math.inf, None, True, "1.0"):
        r = v().check({**GOOD, "joint_3.pos": bad}, None, "right")
        assert not r.ok and "finite" in r.reasons[0]


def test_missing_and_extra_keys_rejected():
    a = dict(GOOD); a.pop("gripper.pos")
    assert "missing" in v().check(a, None, "right").reasons[0]
    assert "unexpected" in v().check({**GOOD, "joint_8.pos": 0.0}, None, "right").reasons[0]
    assert set(ACTION_KEYS) == set(GOOD)


def test_limits_per_side_with_margin():
    # right joint_1 range is (-80, 200); left is (-200, 80): the same value is legal on one side only
    assert not v().check({**GOOD, "joint_1.pos": -79.0}, None, "right").ok  # inside the 2 deg margin
    assert ActionValidator("left", 50.0).check({**GOOD, "joint_1.pos": 70.0}, None, "left").ok is True
    assert not ActionValidator("left", 50.0).check({**GOOD, "joint_1.pos": 79.5}, None, "left").ok


def test_gripper_full_range():
    assert v().check({**GOOD, "gripper.pos": 0.0}, None, "right").ok
    assert v().check({**GOOD, "gripper.pos": -65.0}, None, "right").ok
    assert not v().check({**GOOD, "gripper.pos": 1.0}, None, "right").ok


def test_step_limit():
    lim = 60.0 / 50.0 * 1.5
    assert v().check({**GOOD, "joint_4.pos": 132.0 + lim * 0.9}, GOOD, "right").ok
    r = v().check({**GOOD, "joint_4.pos": 132.0 + lim * 1.2}, GOOD, "right")
    assert not r.ok and "step" in r.reasons[0]


def test_wrong_arm_rejected():
    assert "arm" in v().check(GOOD, None, "left").reasons[0]


def test_radian_heuristic():
    rad = {k: math.radians(x) for k, x in GOOD.items()}
    assert looks_like_radians(rad) and not looks_like_radians(GOOD)
