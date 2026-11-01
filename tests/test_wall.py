"""WALL (log-030): at the workspace edge the arm holds and the hand slips; no FAULT, no LOCK."""
import numpy as np
import pinocchio as pin

from pico_teleop.controller import ENGAGED, FAULT, HOLD
from test_orientation import ADAPT, _arm, _c


def _room(arm):
    return np.rad2deg(np.minimum(arm.q - arm.kin.lo, arm.kin.hi - arm.q))


def test_pushing_into_a_joint_limit_holds_without_fault_or_lock():
    """Left start pose: pitch up runs joint_7 into its limit (log-027). Keep pushing for 10 s."""
    arm = _arm("left")
    events = []
    for i in range(1, 600):
        out = arm.step(ADAPT(_c((0, 1, 0), -min(0.1 * i, 40.0)), True))
        events.append(out.event or "")
        assert out.state == ENGAGED, out.event
        assert _room(arm).min() >= arm.cfg.limit_guard_deg - 1e-6
    assert not arm.locked
    assert any("WALL" in e for e in events)
    assert sum(1 for e in events if "WALL" in e) <= 2      # one message per contact, no chatter


def test_moving_back_from_the_wall_moves_the_arm_at_once():
    arm = _arm("left")
    for i in range(1, 500):
        arm.step(ADAPT(_c((0, 1, 0), -min(0.1 * i, 40.0)), True))
    assert arm.n_wall_cycles > 0
    j7 = np.rad2deg(arm.q[6])
    for i in range(1, 60):                                    # turn back 6 deg from the pushed pose
        arm.step(ADAPT(_c((0, 1, 0), -40.0 + 0.1 * i), True))
    assert abs(np.rad2deg(arm.q[6]) - j7) > 2.0               # the wrist follows the way back immediately
    assert arm.state == ENGAGED


def test_resqueeze_at_the_wall_does_not_lock():
    """The pattern behind the three locks today: edge -> release -> squeeze again -> push."""
    arm = _arm("left")
    for _ in range(4):
        for i in range(1, 300):
            arm.step(ADAPT(_c((0, 1, 0), -min(0.2 * i, 40.0)), True))
        arm.step(ADAPT(_c(grip=0.0), True))
        arm.step(ADAPT(_c(grip=0.0), True))
        assert arm.step(ADAPT(_c(), True)).state == ENGAGED
    assert not arm.locked and arm.state == ENGAGED


def test_no_creep_past_the_session_drift_limit():
    """Long sideways push with re-squeezes: drift stays within its limit, guard distance kept."""
    arm = _arm(orientation=False, max_session_drift_deg=20.0, j1_session_drift_deg=None)
    for rep in range(5):
        for i in range(1, 300):
            arm.step(ADAPT(_c(delta=(0.0, 0.003 * i, 0.0)), True))
        arm.step(ADAPT(_c(grip=0.0), True))
        arm.step(ADAPT(_c(), True))
        j, drift, lim = arm._worst_drift(arm.q)
        assert drift <= lim + 1e-6
    assert not arm.locked


def test_sideways_motion_still_works_at_a_wall():
    """Goldfish #1: one blocked direction must not freeze the others."""
    arm = _arm("left")
    for i in range(1, 500):
        arm.step(ADAPT(_c((0, 1, 0), -min(0.1 * i, 40.0)), True))
    assert arm.n_wall_cycles > 0
    p0 = arm.kin.fk(arm.q).translation.copy()
    for i in range(1, 100):  # keep the turn, move the hand up 6 cm
        arm.step(ADAPT(_c((0, 1, 0), -40.0, delta=(0.0, 0.0, 0.0006 * i)), True))
    assert arm.kin.fk(arm.q).translation[2] - p0[2] > 0.01


def test_deep_singularity_is_still_a_fault():
    """Goldfish #3: below the hard floor (0.02) a stretched pose FAULTs as before."""
    arm = _arm(orientation=False, min_sigma=0.03, min_sigma_hard=0.5)
    out = arm.step(ADAPT(_c(delta=(0.0005, 0.0, 0.0)), True))
    assert out.state == FAULT and "singular" in out.event


def test_wall_state_is_logged_for_episode_filtering():
    arm = _arm("left")
    assert arm.wall is None and arm.n_wall_cycles == 0
    for i in range(1, 500):
        arm.step(ADAPT(_c((0, 1, 0), -min(0.1 * i, 40.0)), True))
    assert arm.n_wall_cycles > 0 and arm.slip_m >= 0.0 and arm.slip_deg > 0.0


def test_ik_jump_holds_instead_of_locking():
    """log-030b: the pilot replay locked on 'IK jumped' twice near a stretched arm. Now it holds."""
    arm = _arm(orientation=False, max_ik_jump_deg=0.001)   # every step counts as a jump
    q0 = arm.q.copy()
    events = [arm.step(ADAPT(_c(delta=(0.0005 * i, 0.0, 0.0)), True)).event for i in range(1, 100)]
    assert arm.state == ENGAGED and not arm.locked
    assert any(e and "joints would jump" in e for e in events)
    assert np.allclose(arm.q, q0)                          # the jump is never sent


def test_acceleration_limit_eases_in_without_overshoot_and_stops_on_release():
    """log-032: with max_joint_acc_dps2 the joint speed ramps up/down; release still stops at once."""
    import math
    arm = _arm(orientation=False, hz=60.0, max_joint_acc_dps2=300.0)
    speeds = []
    for i in range(1, 120):
        q0 = arm.q.copy()
        arm.step(ADAPT(_c(delta=(0.002 * i, 0.0, 0.0)), True))
        speeds.append(np.rad2deg(np.abs(arm.q - q0)).max() * 60.0)
    acc = np.abs(np.diff(speeds)) * 60.0
    assert max(speeds) <= 45.0 + 1e-6 and acc.max() <= 300.0 + 1.0
    arm.step(ADAPT(_c(grip=0.0), True))
    q_rel = arm.q.copy()
    arm.step(ADAPT(_c(grip=0.0), True))
    assert np.allclose(arm.q, q_rel) and not np.any(arm.qd)   # HOLD: no drift after release
