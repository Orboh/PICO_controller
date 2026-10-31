"""Sender-side use of follower feedback (proposals/lerobot-follower-feedback.patch), with a stub link."""
import time

from pico_teleop.controller import ENGAGED, HOLD, TeleopConfig
from pico_teleop.kinematics import DEFAULT_TCP_OFFSET_M, DEFAULT_URDF
from pico_teleop.poses import load_start_pose
from pico_teleop.sender import ArmChannel
from pico_teleop.xr_input import ControllerSample

ID = (0.0, 0.0, 0.0, 1.0)


class StubLink:
    def __init__(self):
        self.last_echo_t = time.monotonic()
        self.follower, self.follower_t, self.sent = None, None, []

    def poll(self):
        self.last_echo_t = time.monotonic()
        return None

    def send(self, msg):
        self.sent.append(msg)


def _ch(follower_offset_deg):
    link = StubLink()
    ch = ArmChannel("right", load_start_pose("right"), TeleopConfig(), link, DEFAULT_URDF, 1.0,
                    DEFAULT_TCP_OFFSET_M)
    link.follower = {k: v + (follower_offset_deg if k == "joint_2.pos" else 0.0) for k, v in ch.last_sent.items()}
    link.follower_t = time.monotonic()
    return ch


def _tick(ch, grip, i):
    c = ControllerSample((0.2, 1.0 + 1e-6 * i, -0.4) + ID, grip, 1.0)
    return ch.tick(c, True, "ok/ok", time.monotonic())


def test_engages_when_follower_matches():
    ch = _ch(3.0)
    _tick(ch, 0.0, 0)
    _tick(ch, 1.0, 1)
    assert ch.arm.state == ENGAGED


def test_refuses_to_engage_when_follower_is_elsewhere():
    ch = _ch(12.0)  # e.g. the follower sagged or aligned to another pose set
    _tick(ch, 0.0, 0)
    _, ev = _tick(ch, 1.0, 1)
    assert ch.arm.state == HOLD


def test_tracking_error_while_engaged_faults():
    ch = _ch(0.0)
    _tick(ch, 0.0, 0); _tick(ch, 1.0, 1)
    assert ch.arm.state == ENGAGED
    ch.link.follower = {**ch.link.follower, "joint_4.pos": ch.last_sent["joint_4.pos"] - 9.0}
    _, ev = _tick(ch, 1.0, 2)
    assert ch.arm.state == "FAULT" and "follower joint_4" in ev
