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
        self.foreign_echo += getattr(self, "foreign_next", 0)
        self.foreign_next = 0
        if not self.silent:
            self.last_echo_t = time.monotonic()
        cmd, self.cmd = self.cmd, None
        return cmd

    def send(self, msg):
        self.sent.append(msg)

    foreign_echo = 0


def _ch(follower_offset_deg):
    link = StubLink()
    ch = ArmChannel("right", load_start_pose("right"), TeleopConfig(engage_hold_s=0.0), link, DEFAULT_URDF, 1.0,
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


def test_link_loss_faults_but_keeps_sending_hold():
    """Elephant m4 #6: a silent receiver (episode save) must not end the session."""
    ch = _ch(0.0)
    _tick(ch, 0.0, 0); _tick(ch, 1.0, 1)
    assert ch.arm.state == ENGAGED
    ch.link.silent, ch.link.last_echo_t = True, time.monotonic() - 2.0
    act, ev = _tick(ch, 1.0, 2)
    assert "LINK DOWN" in ev and ch.arm.state == "FAULT"
    held = act
    for i in range(5):  # keeps sending the same HOLD target, cannot engage
        act, _ = _tick(ch, 0.0 if i == 0 else 1.0, 3 + i)
        assert act == held and ch.arm.state != ENGAGED
    assert len(ch.link.sent) == 8
    ch.link.silent = False
    ch.link.last_echo_t = time.monotonic()
    ch.link.first_echo_t = ch.link.last_echo_t - 1.0
    _, ev = _tick(ch, 0.0, 9)
    assert "link back" in ev


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


def test_long_link_pause_stops_sending_for_good():
    """Elephant m5 #1: a restarted receiver must not get our held pose as its first target."""
    ch = _ch(0.0)
    _tick(ch, 0.0, 0)
    ch.link.silent, ch.link.last_echo_t = True, time.monotonic() - 6.0
    act, ev = _tick(ch, 0.0, 1)
    assert act is None and "SENDING STOPPED" in ev
    n = len(ch.link.sent)
    ch.link.silent = False  # echoes come back: still stopped
    assert _tick(ch, 1.0, 2) == (None, None) and len(ch.link.sent) == n


def test_foreign_echo_locks_engaging():
    ch = _ch(0.0)
    _tick(ch, 0.0, 0)
    ch.link.foreign_next = 1  # the next poll sees an echo of a packet we never sent
    _, ev = _tick(ch, 0.0, 1)
    assert "ANOTHER SENDER" in ev
    _tick(ch, 1.0, 2)
    assert ch.arm.state == HOLD and ch.foreign


def test_fault_cleared_by_release_while_link_down():
    """Elephant m5 #2: otherwise the record 'a' key could never run after an episode ended engaged."""
    ch = _ch(0.0)
    _tick(ch, 0.0, 0); _tick(ch, 1.0, 1)
    ch.link.silent, ch.link.last_echo_t = True, time.monotonic() - 2.0
    _tick(ch, 1.0, 2)
    assert ch.arm.state == "FAULT"
    _, ev = _tick(ch, 0.0, 3)
    assert ch.arm.state == HOLD and "fault cleared" in ev


def test_facing_checked_only_when_starting():
    """Body turned ~100 deg: no start. Head turned ~55 deg to watch the arm (log-020): start allowed
    at <= 75 deg, and turning the head while engaged does NOT stop the arm."""
    ch = _ch(0.0)
    c = lambda g, k: ControllerSample((0.2, 1.0 + 1e-6 * k, -0.4) + ID, g, 1.0)
    ch.tick(c(0.0, 0), True, "ok/ok", time.monotonic(), None, 5.0)
    _, ev = ch.tick(c(1.0, 1), True, "ok/ok", time.monotonic(), None, -100.0)
    assert ch.arm.state == HOLD and "when starting" in ev
    ch.tick(c(1.0, 2), True, "ok/ok", time.monotonic(), None, 55.0)
    assert ch.arm.state == ENGAGED
    for k in range(3, 10):  # head swings to watch the arm, even past the limit: still engaged
        ch.tick(c(1.0, k), True, "ok/ok", time.monotonic(), None, 90.0)
    assert ch.arm.state == ENGAGED


def test_headset_facing_from_live_recordings():
    import json, os
    from pico_teleop.sender import headset_facing_deg
    base = os.path.join(os.path.dirname(__file__), "..", "runs")
    for fn, expect in [("ori-dry-left-1-twist.jsonl", -100), ("axis-both-left-20261031.jsonl", 0)]:
        path = os.path.join(base, fn)
        if not os.path.exists(path):
            continue
        rows = [json.loads(l) for l in open(path)][1:]
        yaws = [headset_facing_deg(r["frame"]["headset"]) for r in rows]
        yaws = [y for y in yaws if y is not None]
        assert abs(sorted(yaws)[len(yaws) // 2] - expect) < 10
