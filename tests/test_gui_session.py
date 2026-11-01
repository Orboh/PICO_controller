"""Collection GUI backend (log-038/039): order and safety rules, key passthrough, live status, notes. No robot."""
import datetime as dt
import json
import pathlib
import sys
import time

import pytest

from pico_teleop.gui import session as gs
from pico_teleop.gui.session import CollectionSession, Options, PtyChild, SenderMonitor, SessionError

ECHO_KEYS = [sys.executable, "-u", "-c",
             "import os,sys,tty\ntty.setcbreak(0)\nprint('  録画中 episode 0（30 秒で自動終了）', flush=True)\n"
             "while True:\n b=os.read(0,16)\n if not b: break\n print('KEY', b.hex(), flush=True)\n"
             " if b==b'\\x1b[C': print('INFO Waiting: press a', flush=True)\n if b==b'\\x1b':\n  print('[record] 記録: /nonexistent/run', flush=True); break"]
OPS = dict(operator="Kento")


def _wait(cond, timeout=3.0):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


class FakeSpawn:
    """The teleop child prints a start-pose sha line then waits; the recorder echoes keys."""

    def __init__(self, sha="2ec31fb82bf0", teleop_s=30):
        self.sha, self.teleop_s, self.calls = sha, teleop_s, []

    def __call__(self, argv, env=None, on_line=None):
        self.calls.append((argv, env))
        if "pilot_record.sh" in " ".join(argv):
            return PtyChild(ECHO_KEYS, on_line=on_line)
        return PtyChild([sys.executable, "-u", "-c",
                         f"import time\nprint('[sender] left: start pose sha256 {self.sha} ok', flush=True)\ntime.sleep({self.teleop_s})"],
                        on_line=on_line)


def _sess(tmp_path, **kw):
    kw.setdefault("links", lambda m, side: [])
    return CollectionSession(runs_dir=tmp_path, spawn=kw.pop("spawn", FakeSpawn()), probe=lambda: [],
                             gng=kw.pop("gng", lambda side: (True, "ok")), **kw)


@pytest.fixture
def sess(tmp_path):
    s = _sess(tmp_path)
    yield s
    for c in (s.recorder, s.teleop):
        if c is not None and c.alive:
            c.proc.kill()


def _ready(s, method="pico"):
    s.start_teleop(Options(method=method, **OPS))
    assert _wait(lambda: s.sha_line()[1]) or method == "leader"
    s.confirm_power("Suraj")
    s.start_recorder()
    assert _wait(lambda: s.episode == 0)


def test_commands_for_both_methods(tmp_path):
    p = gs.teleop_command(Options(method="pico", side="right", smooth=True, wrist=True, premove_cm=10), tmp_path / "x.jsonl")
    assert "--arms" in p and "right" in p and "--orientation" in p and "--premove-forward-cm" in p
    assert p[p.index("--hz") + 1] == "60" and p[p.index("--max-joint-acc-dps2") + 1] == "300"
    assert gs.teleop_command(Options(method="leader", side="left"), tmp_path / "x")[-1] == "left"
    argv, env = gs.recorder_command(Options(method="leader", side="right", episodes=3, operator="Kento"))
    assert argv[-2:] == ["leader", "3"] and env["SIDE"] == "right" and env["OPERATOR"] == "Kento"


def test_refusals_before_start(tmp_path):
    s = CollectionSession(runs_dir=tmp_path, spawn=FakeSpawn(), probe=lambda: ["leader_agent"], gng=lambda side: (True, ""))
    with pytest.raises(SessionError, match="another program"):
        s.start_teleop(Options(**OPS))
    s = _sess(tmp_path)
    with pytest.raises(SessionError, match="operator"):
        s.start_teleop(Options(operator=""))
    s = _sess(tmp_path, gng=lambda side: (False, "go/no-go items not PASS: 3"))
    with pytest.raises(SessionError, match="not PASS"):
        s.start_teleop(Options(**OPS))


def test_order_and_one_recorder_per_teleop(sess):
    with pytest.raises(SessionError, match="teleop first"):
        sess.start_recorder()
    sess.start_teleop(Options(**OPS))
    assert _wait(lambda: sess.sha_line()[1])
    with pytest.raises(SessionError, match="spotter"):
        sess.start_recorder()
    with pytest.raises(SessionError, match="spotter's name"):
        sess.confirm_power("")
    sess.confirm_power("Suraj")
    sess.start_recorder()
    assert _wait(lambda: sess.episode == 0 and sess.recording_now)
    sess.key("next")
    assert _wait(lambda: any("KEY 1b5b43" in l for l in sess.recorder.lines))   # right arrow reached the recorder
    assert _wait(lambda: not sess.recording_now)                                   # "Waiting:" ends the episode
    with pytest.raises(SessionError, match="end the recording first"):
        sess.stop_teleop()
    sess.key("stop")
    assert _wait(lambda: not sess.recorder_running)
    sess.poll()
    with pytest.raises(SessionError, match="one recording per teleop"):    # Elephant GUI #1 (R2)
        sess.start_recorder()
    with pytest.raises(SessionError, match="already had a recorder"):
        sess.confirm_power("Suraj")
    assert sess.run_dir == "/nonexistent/run"


def test_wrong_start_pose_sha_blocks_the_recorder(tmp_path):
    s = _sess(tmp_path, spawn=FakeSpawn(sha="deadbeef0000"))
    s.start_teleop(Options(**OPS))
    assert _wait(lambda: s.sha_line()[0] is not None)
    s.confirm_power("Suraj")
    with pytest.raises(SessionError, match="sha"):
        s.start_recorder()
    s.teleop.proc.kill()


def test_r4_engaged_refusals_and_stale_status(sess):
    _ready(sess)
    st = sess.monitor.status
    st.state, st.read_at = "ENGAGED", time.monotonic()
    for k in ("stop", "reset", "rerecord"):
        with pytest.raises(SessionError, match="release the grip"):
            sess.key(k)
    st.read_at = time.monotonic() - 5.0          # a crashed sender's last line is not trusted (Elephant GUI #2)
    sess.key("reset")


def test_locked_and_foreign_refuse_next(sess, tmp_path):
    _ready(sess)
    sess.monitor.status.locked = True
    with pytest.raises(SessionError, match="LOCKED"):
        sess.key("next")
    p = tmp_path / "f.jsonl"
    p.write_text(json.dumps({"t": 1.0, "frame": {}, "arms": {"left": {"state": "FAULT", "event":
                 "FAULT: foreign echo | echo of packets we did not send: ANOTHER SENDER on this port. Engaging is locked"}}}) + "\n")
    m = SenderMonitor(p, "left")
    assert m.poll().locked and m.status.foreign


def test_leader_needs_hands_and_held_confirmations(sess):
    _ready(sess, method="leader")
    with pytest.raises(SessionError, match="leader arm is held"):
        sess.key("stop")
    sess.key("stop", hands_off=True)
    assert _wait(lambda: not sess.recorder_running)
    with pytest.raises(SessionError, match="LEADER arm"):
        sess.stop_teleop()
    sess.stop_teleop(leader_held=True)


def test_premove_only_after_power_and_recorder(sess):
    sess.start_teleop(Options(premove_cm=10, **OPS))
    with pytest.raises(SessionError, match="after"):
        sess.premove()


def test_rerecord_discards_the_notes_and_shutdown_keeps_a_leader_agent(sess):
    _ready(sess, method="leader")
    sess.set_note(0, grasp=True)
    sess.key("rerecord")
    assert 0 not in sess.notes
    msgs = sess.shutdown()
    assert any("LEADER AGENT LEFT RUNNING" in m for m in msgs) and sess.teleop_running


def test_buffer_rollover_keeps_new_lines(tmp_path):
    c = PtyChild([sys.executable, "-u", "-c", "for i in range(600): print('line', i)"], tail=100)
    assert _wait(lambda: c.count >= 600)
    lines, n = c.new_lines(590)
    assert lines == [f"line {i}" for i in range(590, 600)] and n == 600


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
    assert 25 <= s.input_hz <= 31 and m.events[-1][1].startswith("WALL") and m.fresh()


def test_recorder_patterns_match_the_real_console_log():
    log = pathlib.Path.home() / "collections" / "20261101T105857Z-pilot-pico" / "console.log"
    if not log.exists():
        pytest.skip("real console.log not on this PC")
    text = log.read_text(encoding="utf-8", errors="replace")
    assert gs.RECORDING.search(text) and gs.WAITING.search(text)
    assert gs.RUN_DIR.search(text).group(1).endswith("pilot-pico")


def test_go_no_go_parse(tmp_path):
    rb = tmp_path / "rb.md"
    rb.write_text("| # | Item | x | 2026-11-02 |\n|---|---|---|---|\n| 1 | a | b | PASS |\n| 3 | e | f | FAIL: no e-stop |\n"
                  "| 5 | arm | c | left |\n")
    ok, why = gs.go_no_go_today(rb, dt.date(2026, 11, 2))
    assert not ok and "3" in why
    rb.write_text("| # | Item | x | 2026-11-02 |\n|---|---|---|---|\n| 1 | a | b | PASS |\n| 5 | arm | c | left |\n")
    assert gs.go_no_go_today(rb, dt.date(2026, 11, 2), side="left")[0]
    ok, why = gs.go_no_go_today(rb, dt.date(2026, 11, 2), side="right")
    assert not ok and "item 5" in why
    assert "no go/no-go column" in gs.go_no_go_today(rb, dt.date(2026, 11, 3))[1]


def test_go_no_go_real_runbook_today_parses():
    ok, why = gs.go_no_go_today(today=dt.date(2026, 11, 1), side="left")
    assert ok, why


def test_notes_are_saved(sess):
    sess.start_teleop(Options(method="leader", **OPS))
    sess.set_note(0, grasp=True, text="dropped at the bin edge")
    data = json.loads(next(sess.runs_dir.glob("*-notes.json")).read_text())
    assert data["episodes"]["0"]["grasp"] is True and data["options"]["method"] == "leader"


def test_stage_repo_ids_and_session_needs_a_reviewed_gate(tmp_path):
    assert gs.repo_id(Options(stage="dry", method="pico")) == "Orboh/openarm_pilot_pico_tmp"
    assert gs.repo_id(Options(stage="gate", method="leader", side="right")) == "Orboh/openarm_leader_right_gate"
    s = _sess(tmp_path, reviews=tmp_path / "rev.json")
    with pytest.raises(SessionError, match="reviewed gate"):
        s.start_teleop(Options(stage="session", scene="kitakyushu-takeei-v1", **OPS))
    s.gate_done = {"method": "pico", "side": "left", "tag": "t", "dataset": None, "episodes": 10}
    with pytest.raises(SessionError, match="reviewer's name"):
        s.record_review("")
    s.record_review("Kota")
    assert s.gate_ok("pico", "left")["reviewer"] == "Kota" and s.gate_ok("pico", "right") is None
    with pytest.raises(SessionError, match="scene profile"):
        s.start_teleop(Options(stage="session", **OPS))
    s.start_teleop(Options(stage="session", scene="kitakyushu-takeei-v1", **OPS))
    s.teleop.proc.kill()


def test_checks_and_translations(tmp_path, monkeypatch):
    from pico_teleop.gui import checks, i18n
    monkeypatch.setattr(checks, "_run", lambda argv, timeout=3.0: "wlp0s20f3 UP 192.168.0.136/24\n" if argv[0] == "ip" else "orboh\n")
    assert checks.wifi().ok
    monkeypatch.setattr(checks, "HEADSET_IP", "172.20.10.3")      # phone tethering: other network
    assert not checks.wifi().ok
    monkeypatch.setattr(checks, "_run", lambda argv, timeout=3.0: "wlp0s20f3 UP 172.20.10.2/28\n" if argv[0] == "ip" else "Kota-iPhone\n")
    assert checks.wifi().ok
    monkeypatch.setattr(checks, "_run", lambda argv, timeout=3.0: "LISTEN 0 4096 [::ffff:127.0.0.1]:60061 *:*\n")
    assert checks.service().ok
    monkeypatch.setattr(checks, "_run", lambda argv, timeout=3.0: "rate ~8 Hz | L ok | R ok | facing 3 deg  (OK)")
    assert checks.pico_input().ok is False
    assert all(len(v) == 2 and v[1] for v in i18n.TEXT.values())
    assert i18n.refusal("the operator is still squeezing: release", "ja").startswith("操作者がまだ握って")
    assert i18n.t("arm", "ja", s="左") == "左腕"


def test_spotter_differs_can_down_and_stuck_recorder(tmp_path):
    s = _sess(tmp_path, links=lambda m, side: ["can_fol_l down"])
    s.start_teleop(Options(**OPS))
    assert _wait(lambda: s.sha_line()[1])
    with pytest.raises(SessionError, match="different person"):
        s.confirm_power("kento")
    s.confirm_power("Suraj")
    with pytest.raises(SessionError, match="CAN link down"):
        s.start_recorder()
    s.links = lambda m, side: []
    s.recorder_used = False
    s.start_recorder()
    s._recorder_line("ConnectionError: Failed to connect to CAN bus: Network is down")
    assert s.recorder_stuck() and "RECORDER FAILED" in s.alarm
    with pytest.raises(SessionError, match="confirm"):
        s.abort_recorder()
    s.abort_recorder(confirmed=True)
    assert _wait(lambda: not s.recorder_running)
    s.teleop.proc.kill()


def test_recorder_runs_without_a_display(monkeypatch):
    """log-042: lerobot uses the global keyboard when DISPLAY is set; the page's keys need it removed."""
    argv, env = gs.recorder_command(Options(**OPS))
    assert env["DISPLAY"] is None and env["WAYLAND_DISPLAY"] is None
    monkeypatch.setenv("DISPLAY", ":1")
    c = PtyChild([sys.executable, "-c", "import os; print('DISPLAY', os.environ.get('DISPLAY'))"], env=env)
    assert _wait(lambda: any(l.startswith("DISPLAY") for l in c.lines))
    assert "DISPLAY None" in c.lines


def test_right_arrow_reaches_lerobots_real_key_reader(monkeypatch):
    """log-042: end to end with lerobot's own listener (skipped where lerobot is not installed)."""
    py = pathlib.Path.home() / "projects" / "lerobot" / ".venv" / "bin" / "python"
    if not py.exists():
        pytest.skip("lerobot venv not on this PC")
    monkeypatch.setenv("DISPLAY", ":1")                      # like the desktop session the GUI runs in
    code = ("import time\nfrom lerobot.common.control_utils import init_keyboard_listener\n"
            "l, ev = init_keyboard_listener()\nprint('READY', flush=True)\nt=time.time()\n"
            "while time.time()-t<3 and not ev['exit_early']: time.sleep(0.05)\nprint('NEXT', ev['exit_early'], flush=True)\n")
    _, env = gs.recorder_command(Options(**OPS))
    c = PtyChild([str(py), "-c", code], env={"DISPLAY": env["DISPLAY"], "WAYLAND_DISPLAY": env["WAYLAND_DISPLAY"]})
    assert _wait(lambda: any("READY" in l for l in c.lines), 15)
    c.send(gs.KEYS["next"])
    assert _wait(lambda: any(l == "NEXT True" for l in c.lines), 6)


def test_both_arms_leader_uses_collect_bimanual_and_pico_both_is_refused(tmp_path):
    o = Options(method="leader", side="both", stage="session", tag="s2", episodes=10, **OPS)
    argv, env = gs.recorder_command(o)
    assert argv[-4:] == ["collect_bimanual.sh", "session", "s2", "10"][-4:] or argv[1].endswith("collect_bimanual.sh")
    assert argv[2:] == ["session", "s2", "10"] and env["ENV_FILE"].endswith("kitakyushu_takeei_sort.env")
    assert env["DISPLAY"] is None and env["OPERATOR"] == "Kento"
    assert "sleep infinity" in " ".join(gs.teleop_command(o, tmp_path / "x"))
    assert gs.recorder_command(Options(method="leader", side="both", stage="dry", episodes=2, **OPS))[0][2:] == ["dry", "2"]
    s = _sess(tmp_path)
    with pytest.raises(SessionError, match="both arms with the PICO"):
        s.start_teleop(Options(method="pico", side="both", **OPS))
    with pytest.raises(SessionError, match="type a tag"):
        s.start_teleop(Options(method="leader", side="both", stage="dry", **OPS) if False else Options(method="leader", side="both", stage="session", **OPS))
    downs = gs.links_down("leader", "both")
    assert all(n.split()[0] in ("can_fol_l", "can_fol_r", "can_lead_l", "can_lead_r") for n in downs)
    assert gs.RUN_DIR.search("[record-bi] 記録: /home/x/collections/abc").group(1) == "/home/x/collections/abc"



def test_only_a_complete_gate_is_reviewed_and_a_new_gate_clears_the_ok(tmp_path):
    """Elephant 11-01 #1: exit 0 and all episodes saved, else no review; a new gate cancels the old OK."""
    rev = tmp_path / "rev.json"
    s = _sess(tmp_path, reviews=rev)
    s.opts = Options(stage="gate", episodes=10, **OPS)
    s.tag, s.run_dir = "t", str(tmp_path)
    (tmp_path / "manifest.json").write_text(json.dumps({"artifacts": {"episodes_recorded": 0, "dataset_root": "/d"}}))

    class Dead:
        alive = False
        proc = type("P", (), {"returncode": 0})()
    s.recorder, s._recorder_was_alive = Dead(), True
    s.poll()
    assert s.gate_done is None and "NO EPISODE WAS SAVED" in s.alarm
    (tmp_path / "manifest.json").write_text(json.dumps({"artifacts": {"episodes_recorded": 10, "dataset_root": "/d"}}))
    s.alarm, s._recorder_was_alive = "", True
    s.poll()
    assert s.gate_done and s.gate_done["episodes"] == 10 and json.loads(rev.read_text())["pending"]
    s.record_review("Kota")
    assert s.gate_ok("pico", "left") and "pending" not in json.loads(rev.read_text())
    s2 = _sess(tmp_path, reviews=rev)
    s2.start_teleop(Options(stage="gate", scene="kitakyushu-takeei-v1", **OPS))
    assert s2.gate_ok("pico", "left") is None
    s2.teleop.proc.kill()


def test_stuck_after_60s_and_error_regex_ignores_raise_lines(sess):
    _ready(sess)
    sess._recorder_line("    raise ConnectionError(f'Failed to connect')")
    sess._recorder_line("INFO something Error-free happened")
    assert not sess.recorder_error
    sess.end_pressed_at = time.monotonic() - 30
    assert not sess.recorder_stuck()
    sess.end_pressed_at = time.monotonic() - 61
    assert sess.recorder_stuck()


def test_every_refusal_has_japanese():
    import inspect
    import re as _re
    from pico_teleop.gui import i18n
    src = inspect.getsource(gs)
    msgs = _re.findall(r'SessionError\(f?"([^"{]{8,})', src)
    prefixes = [p for p, _ in i18n.REFUSALS_JA]
    missing = [m for m in msgs if not any(m.startswith(p) for p in prefixes)]
    assert not missing, missing


def test_recorder_scripts_drop_display():
    for name in ("pilot_record.sh", "gate4_record.sh"):
        assert "env -u DISPLAY -u WAYLAND_DISPLAY" in (gs.REPO / "scripts" / name).read_text()
