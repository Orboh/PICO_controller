"""ArmChannel logic with a stub link (no sockets): follower feedback, link states, soft_move_to gating."""
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
        self.first_echo_t = self.last_echo_t - 1.0   # echoes for 1 s already: link "ok"
        self.follower, self.follower_t, self.sent = None, None, []
        self.cmd, self.silent, self.rtt_ms = None, False, None

    def poll(self, gap_s):
        if not self.silent:
            self.last_echo_t = time.monotonic()
        cmd, self.cmd = self.cmd, None
        return cmd

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


def test_engage_refused_is_reported():
    ch = _ch(12.0)
    _tick(ch, 0.0, 0)
    _, ev = _tick(ch, 1.0, 1)
    assert ev and ev.startswith("engage refused: follower joint_2")


def test_no_engage_before_receiver_echoes():
    ch = _ch(0.0)
    ch.link.last_echo_t = ch.link.first_echo_t = None
    ch.link.silent = True
    _tick(ch, 0.0, 0)
    _, ev = _tick(ch, 1.0, 1)
    assert ch.arm.state == HOLD and "waiting for the receiver" in ev
    assert len(ch.link.sent) == 2  # still sending the start pose (the receiver needs a first packet)


def test_link_loss_faults_and_stops_sending():
    ch = _ch(0.0)
    _tick(ch, 0.0, 0); _tick(ch, 1.0, 1)
    assert ch.arm.state == ENGAGED
    ch.link.silent, ch.link.last_echo_t = True, time.monotonic() - 2.0
    act, ev = _tick(ch, 1.0, 2)
    assert act is None and "SENDING STOPPED" in ev and ch.arm.state == "FAULT"
    n = len(ch.link.sent)
    assert _tick(ch, 1.0, 3) == (None, None) and len(ch.link.sent) == n


def test_soft_move_only_in_hold_and_checked():
    ch = _ch(0.0)
    pose = {k[:-4]: v for k, v in ch.last_sent.items()}
    _tick(ch, 0.0, 0); _tick(ch, 1.0, 1)
    ch.link.cmd = {"cmd": "soft_move_to", "cmd_id": 1, "pose": pose, "duration_s": 2.0}
    _, ev = _tick(ch, 1.0, 2)
    assert "ignored: arm is ENGAGED" in ev and ch.move is None
    _tick(ch, 0.0, 3)  # release -> HOLD
    ch.link.cmd = {"cmd": "soft_move_to", "cmd_id": 2, "pose": {**pose, "joint_1": 500.0}, "duration_s": 2.0}
    _, ev = _tick(ch, 0.0, 4)
    assert "outside the limits" in ev and ch.move is None
    ch.link.cmd = {"cmd": "soft_move_to", "cmd_id": 3, "pose": {**pose, "joint_1": pose["joint_1"] + 5.0}}
    _, ev = _tick(ch, 0.0, 5)
    assert ch.move is not None
    events = [_tick(ch, 0.0, 6 + i)[1] for i in range(20)]
    assert any(e and "arrived" in e for e in events)
