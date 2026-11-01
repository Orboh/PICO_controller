"""Collection GUI backend (log-038): order and safety rules, key passthrough, live status, notes. No robot."""
import datetime as dt
import json
import sys
import time

import pytest

from pico_teleop.gui import session as gs
from pico_teleop.gui.session import CollectionSession, Options, PtyChild, SenderMonitor, SessionError

ECHO_KEYS = [sys.executable, "-u", "-c",
             "import os,sys,tty\ntty.setcbreak(0)\nprint('Recording episode 0', flush=True)\n"
             "while True:\n b=os.read(0,16)\n if not b: break\n print('KEY', b.hex(), flush=True)\n if b==b'\\x1b': break"]


def _wait(cond, timeout=3.0):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


class FakeSpawn:
    """Records the commands; the teleop child prints a start-pose sha line, the recorder echoes keys."""

    def __init__(self, sha="2ec31fb82bf0"):
        self.sha, self.calls = sha, []

    def __call__(self, argv, env=None, on_line=None):
        self.calls.append((argv, env))
        if "pilot_record.sh" in " ".join(argv):
            return PtyChild(ECHO_KEYS, on_line=on_line)
        return PtyChild([sys.executable, "-u", "-c",
                         f"import time\nprint('[sender] left: start pose sha256 {self.sha} ok', flush=True)\ntime.sleep(30)"])


@pytest.fixture
def sess(tmp_path):
    s = CollectionSession(runs_dir=tmp_path, spawn=FakeSpawn(), probe=lambda: [])
    yield s
    for c in (s.recorder, s.teleop):
        if c is not None and c.alive:
            c.proc.kill()


def test_commands_for_both_methods(tmp_path):
    p = gs.teleop_command(Options(method="pico", side="right", smooth=True, wrist=True, premove_cm=10), tmp_path / "x.jsonl")
    assert "--arms" in p and "right" in p and "--orientation" in p and "--premove-forward-cm" in p
    assert p[p.index("--hz") + 1] == "60" and p[p.index("--max-joint-acc-dps2") + 1] == "300"
    assert gs.teleop_command(Options(method="leader", side="left"), tmp_path / "x")[-1] == "left"
    argv, env = gs.recorder_command(Options(method="leader", side="right", episodes=3, operator="Kento"))
    assert argv[-2:] == ["leader", "3"] and env["SIDE"] == "right" and env["OPERATOR"] == "Kento"


def test_refuses_when_another_sender_runs(tmp_path):
    s = CollectionSession(runs_dir=tmp_path, spawn=FakeSpawn(), probe=lambda: ["leader_agent"])
    with pytest.raises(SessionError, match="another program"):
        s.start_teleop(Options())


def test_order_teleop_power_recorder_and_keys(sess):
    with pytest.raises(SessionError, match="teleop first"):
        sess.start_recorder()
    sess.start_teleop(Options(method="pico", side="left"))
    assert _wait(lambda: sess.sha_line()[1])
    with pytest.raises(SessionError, match="spotter"):
        sess.start_recorder()
    sess.confirm_power()
    sess.start_recorder()
    assert _wait(lambda: sess.episode == 0)
    sess.key("next")
    assert _wait(lambda: any("KEY 1b5b43" in l for l in sess.recorder.lines))   # right arrow reached the recorder
    with pytest.raises(SessionError, match="end the recording first"):
        sess.stop_teleop()


def test_wrong_start_pose_sha_blocks_the_recorder(tmp_path):
    s = CollectionSession(runs_dir=tmp_path, spawn=FakeSpawn(sha="deadbeef0000"), probe=lambda: [])
    s.start_teleop(Options(method="pico", side="left"))
    assert _wait(lambda: s.sha_line()[0] is not None)
    s.confirm_power()
    with pytest.raises(SessionError, match="sha"):
        s.start_recorder()
    s.teleop.proc.kill()


def test_esc_refused_while_engaged_rule_r4(sess):
    sess.start_teleop(Options(method="pico"))
    _wait(lambda: sess.sha_line()[1])
    sess.confirm_power()
    sess.start_recorder()
    sess.monitor.status.state = "ENGAGED"
    with pytest.raises(SessionError, match="release the grip"):
        sess.key("stop")
    sess.monitor.status.state = "HOLD"
    sess.key("stop")


def test_leader_stop_needs_the_leader_arm_held(sess):
    sess.start_teleop(Options(method="leader"))
    with pytest.raises(SessionError, match="LEADER arm"):
        sess.stop_teleop()
    sess.stop_teleop(leader_held=True)


def test_monitor_reads_state_walls_and_input_rate(tmp_path):
    p = tmp_path / "s.jsonl"
    lines = [json.dumps({"session": "x", "args": {"hz": 50.0}})]
    for k in range(60):
        lines.append(json.dumps({"t": 100 + k * 0.02, "frame": {"t_device_ns": 1000 + k // 2, "headset": None},
                                 "arms": {"left": {"state": "ENGAGED", "link": "ok", "wall": "joint_2 at its limit" if k >= 50 else None,
                                                   "event": "WALL: joint_2 at its limit" if k == 50 else None}}}))
    p.write_text("\n".join(lines) + "\n")
    m = SenderMonitor(p, "left")
    s = m.poll()
    assert s.state == "ENGAGED" and s.wall == "joint_2 at its limit" and s.wall_s == pytest.approx(10 / 50)
    assert 25 <= s.input_hz <= 31 and m.events[-1][1].startswith("WALL")


def test_go_no_go_parse(tmp_path):
    rb = tmp_path / "rb.md"
    rb.write_text("| # | Item | x | 2026-11-02 |\n|---|---|---|---|\n| 1 | a | b | PASS |\n| 3 | e | f | **pending**: x |\n")
    ok, why = gs.go_no_go_today(rb, dt.date(2026, 11, 2))
    assert not ok and "3" in why
    ok, why = gs.go_no_go_today(rb, dt.date(2026, 11, 3))
    assert not ok and "no go/no-go column" in why


def test_notes_are_saved(sess):
    sess.start_teleop(Options(method="leader"))
    sess.set_note(0, grasp=True, text="dropped at the bin edge")
    data = json.loads(next(sess.runs_dir.glob("*-notes.json")).read_text())
    assert data["episodes"]["0"]["grasp"] is True and data["options"]["method"] == "leader"
