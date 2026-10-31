"""Gate 1 (offline part): input validity rules, the shared freshness rule, and the diagnostic scoring."""
import json
import math
import sys
import types

import pytest

from pico_teleop import diag_input as D
from pico_teleop.xr_input import (ControllerSample, FakeSource, FreshnessTracker, ReplaySource, SdkSource,
                                  SingleReaderLock, XRFrame, pose_status)

ID = (0.0, 0.0, 0.0, 1.0)
STILL_L, STILL_R = (-0.2, 1.0, -0.3) + ID, (0.2, 1.0, -0.3) + ID


def test_pose_status_rules():
    assert pose_status((0.1, 1.0, -0.3) + ID) == "ok"
    assert pose_status((0.0,) * 7) == "zero"
    assert pose_status((0.0, 0.0, 0.0) + ID) == "origin"
    assert pose_status((0.1, math.nan, 0.0) + ID) == "nonfinite"
    assert pose_status((0.1, 1.0, 0.0, 0.0, 0.0, 0.0, 0.3)) == "bad_quat"
    assert pose_status((0.1, 1.0)) == "bad_shape"


def _c(pose, g=0.0, t=0.0, **b):
    return ControllerSample(tuple(pose), g, t, **b)


def _jit(pose, i):
    """Real tracking never repeats a pose bit-for-bit; add tiny jitter."""
    return (pose[0] + 1e-5 * (i % 7), pose[1], pose[2]) + tuple(pose[3:])


def _phase(name, n=600, dt=0.01, t0=0.0, ts0=1):
    out = []
    for i in range(n):
        u = i / (n - 1)
        L, R = _c(_jit(STILL_L, i)), _c(_jit(STILL_R, i))
        ts = ts0 + i
        if name in ("left", "right"):
            mv = _c(((STILL_L if name == "left" else STILL_R)[0] + 0.1 * u, 1.0, -0.3) + ID,
                    g=u, t=1 - u, primary=i == 50, secondary=i == 60)
            L, R = (mv, R) if name == "left" else (L, mv)
        elif name == "left_loss" and 100 <= i < 200:
            L = _c(STILL_L)  # frozen: identical pose while the timestamp advances
        elif name == "right_loss" and 100 <= i < 200:
            R = _c((0.0,) * 7)
        elif name == "headset_off" and 100 <= i < 200:
            ts = ts0 + 100  # device timestamp stuck = stream stale
        out.append(XRFrame(t0 + i * dt, ts, L, R))
    return out


def _all_phases():
    res, t = [], 0.0
    for k, (name, _, _) in enumerate(D.PHASES):
        fr = _phase(name, t0=t, ts0=1 + k * 10000)
        res.append(D.score_phase(name, fr, fr[0].t_host))
        t += 8.0
    return res


def test_guided_run_passes():
    text, ok = D.format_report(_all_phases(), "TEST")
    assert ok, text
    assert "event: left frozen" in text and "event: right zero" in text and "event: stream stale" in text
    assert "loss itself is reported" in text


def test_swapped_sides_fail():
    fr = _phase("left")
    swapped = [XRFrame(f.t_host, f.t_device_ns, f.right, f.left) for f in fr]
    checks = D.checks_for(D.score_phase("left", swapped, 0.0))
    assert not checks["left: moved >= 5 cm"]


def test_one_outlier_is_not_a_move():
    fr = _phase("right_loss")[:50]
    fr[25] = XRFrame(fr[25].t_host, fr[25].t_device_ns, _c((0.5, 1.0, -0.3) + ID), fr[25].right)
    assert D.score_phase("x", fr, 0.0).sides["left"].moved() < 0.01


def test_no_data_fails_everything():
    idle = _c((0.0,) * 7)
    fr = [XRFrame(i * 0.01, 0, idle, idle) for i in range(100)]
    r = D.score_phase("left", fr, 0.0)
    assert not any(D.checks_for(r).values())


def test_missing_phase_fails():
    _, ok = D.format_report(_all_phases()[:-1], "TEST")
    assert not ok


def test_freshness_backwards_and_stale():
    tr = FreshnessTracker(stale_s=0.1)
    tr.update(XRFrame(0.0, 100, _c(STILL_L), _c(STILL_R)))
    assert tr.fresh("left")
    tr.update(XRFrame(0.01, 50, _c(_jit(STILL_L, 1)), _c(_jit(STILL_R, 1))))
    assert tr.stream == "ts_backwards" and not tr.fresh("left")
    tr.update(XRFrame(0.02, 60, _c(_jit(STILL_L, 2)), _c(_jit(STILL_R, 2))))
    assert tr.fresh("left")
    tr.update(XRFrame(0.30, 60, _c(_jit(STILL_L, 2)), _c(_jit(STILL_R, 2))))
    assert tr.stream == "stale"


def test_record_replay_roundtrip(tmp_path):
    p = tmp_path / "rec.jsonl"
    fr = _phase("left", n=20)
    p.write_text("".join(json.dumps({"phase": "left", "frame": json.loads(f.to_json())}) + "\n" for f in fr))
    (name, frames, _), = D.load_replay(str(p))
    assert name == "left" and frames == fr


def test_replay_source_exhausts(tmp_path):
    p = tmp_path / "r.jsonl"
    p.write_text("".join(f.to_json() + "\n" for f in _phase("left", n=5)))
    rs = ReplaySource(str(p))
    for _ in range(7):
        rs.read()
    assert rs.exhausted


def test_fake_source_freezes_device_timestamp():
    t = [0.0]
    src = FakeSource([(0.0, {"right": _c(STILL_R)})], clock=lambda: t[0], freeze_after=0.5)
    a = src.read(); t[0] = 1.0; b = src.read(); t[0] = 2.0; c = src.read()
    assert a.t_device_ns < b.t_device_ns == c.t_device_ns


def test_single_reader_lock(tmp_path):
    p = str(tmp_path / "lock")
    a, b = SingleReaderLock(p), SingleReaderLock(p)
    a.acquire()
    with pytest.raises(RuntimeError, match="already reads"):
        b.acquire()
    a.release()
    b.acquire(); b.release()


def test_sdk_init_failure_releases_lock(tmp_path, monkeypatch):
    fake = types.ModuleType("xrobotoolkit_sdk")
    fake.init = lambda: (_ for _ in ()).throw(RuntimeError("PXREAInit failed"))
    monkeypatch.setitem(sys.modules, "xrobotoolkit_sdk", fake)
    p = str(tmp_path / "lock")
    with pytest.raises(RuntimeError, match="PXREAInit"):
        SdkSource(SingleReaderLock(p))
    SingleReaderLock(p).acquire()  # would raise if the lock leaked
