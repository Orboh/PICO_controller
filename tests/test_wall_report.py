"""tools/wall_report.py (log-047): per-episode wall time / slip from the page's notes or the sender log."""
import json
import sys
import pathlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import wall_report as wr  # noqa: E402


def _session(tmp_path, method="pico"):
    log = tmp_path / "s-sender.jsonl"
    rows = [json.dumps({"session": "x", "args": {"hz": 50.0}})]
    for k in range(500):                        # 10 s; episode 0 = 0-4.98 s, episode 1 = 5-9.98 s
        t = 100 + k * 0.02
        wall = (k >= 250 and k < 450)           # episode 1: 4 s at a wall
        slip = 0.0 if k < 250 else 0.0015 * (k - 250)   # episode 1: ~37 cm of slip
        rows.append(json.dumps({"t": t, "arms": {"left": {"wall": "joint_2" if wall else None, "slip_m": slip}}}))
    log.write_text("\n".join(rows) + "\n")
    notes = tmp_path / "gui-x-notes.json"
    notes.write_text(json.dumps({"tag": "gui-x", "options": {"method": method, "side": "left", "stage": "gate"},
                                 "pose_set": "set-a", "sender_log": str(log), "episodes": {
                                     "0": {"wall_s": 0.0, "slip_cm": 0.0, "sender_t": [100.0, 104.98], "text": ""},
                                     "1": {"wall_s": 1.0, "slip_cm": 2.0, "sender_t": [105.0, 109.98], "text": "x"}}}))
    return notes


def test_page_values_and_recompute_from_the_log(tmp_path):
    notes = _session(tmp_path)
    page = wr.rows([str(notes)], False, 3.0, 20.0)
    assert [r["wall_s"] for r in page] == [0.0, 1.0] and not any(r["flag"] for r in page)
    log = wr.rows([str(notes)], True, 3.0, 20.0)
    assert log[0]["wall_s"] == 0.0 and log[0]["slip_cm"] == pytest.approx(0.0)
    assert log[1]["wall_s"] == pytest.approx(4.0) and log[1]["slip_cm"] == pytest.approx(37.35, abs=0.1)
    assert "wall" in log[1]["flag"] and "slip" in log[1]["flag"] and log[1]["source"] == "log"


def test_leader_never_flagged_and_json_report(tmp_path, capsys):
    notes = _session(tmp_path, method="leader")
    assert not any(r["flag"] for r in wr.rows([str(notes)], True, 3.0, 20.0))
    (tmp_path / "p").mkdir()
    pico = _session(tmp_path / "p")
    assert wr.main([str(pico), "--from-log", "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["pico_episodes"] == 2 and len(rep["flagged"]) == 1 and rep["wall_s"]["max"] == pytest.approx(4.0)
    assert rep["thresholds"]["provisional"] and sum(n for _, n in rep["wall_s_histogram"]) == 2
    assert wr.main([str(pico), "--wall-s", "10", "--slip-cm", "50", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["flagged"] == []


def test_quantiles():
    q = wr.quantiles([0, 1, 2, 3, 4])
    assert q["median"] == 2 and q["max"] == 4 and q["n"] == 5 and wr.quantiles([]) == {"n": 0}
