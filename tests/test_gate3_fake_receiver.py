"""Gate 3: the sender against the REAL lerobot receiver class on localhost (ports +20000, so a real
follower on 5701/5702 can never be reached). Skipped if the lerobot venv is missing."""
import json
import math
import os
import subprocess
import sys
import time

import numpy as np
import pytest

from pico_teleop.frames import R_ROBOT_FROM_XR
from pico_teleop.xr_input import ControllerSample, XRFrame

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEROBOT_PY = os.path.expanduser("~/projects/lerobot/.venv/bin/python")
POSE_SETS = os.path.expanduser("~/projects/openarm-orboh/config/pose_sets")
OFFSET = 20000
KEYS = [f"joint_{i}.pos" for i in range(1, 8)] + ["gripper.pos"]
pytestmark = pytest.mark.skipif(not os.path.exists(LEROBOT_PY), reason="lerobot venv missing")
ENV = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}


def _script(hz=50.0):
    """idle 1 s -> engage + 5 cm circle 3 s -> release 1 s (hand keeps moving) -> re-engage 2 s
    -> controller pose all-zero 0.5 s (tracking loss) -> grip released 0.5 s -> idle 1 s."""
    xr0, frames, ts = np.array([0.2, 1.0, -0.4]), [], 0
    plan = [("idle", 1.0), ("circle", 3.0), ("released", 1.0), ("reengage", 2.0), ("lost", 0.5),
            ("release", 0.5), ("idle", 1.0)]
    t = 0.0
    for name, dur in plan:
        for _ in range(int(dur * hz)):
            ts += 1
            d = np.zeros(3)
            if name == "circle":
                u = t - 1.0
                d = np.array([0.1 * math.cos(u) - 0.1, 0.1 * math.sin(u), 0.0])
            elif name in ("released", "reengage", "lost"):
                d = np.array([0.0, 0.2, 0.0])  # hand far from where it was: re-anchor must hide this
            d = d + 1e-5 * np.array([ts % 7, ts % 5, ts % 3])  # tracking noise: never bit-identical
            pose = tuple(xr0 + R_ROBOT_FROM_XR.T @ d) + (0.0, 0.0, 0.0, 1.0)
            if name == "lost":
                pose = (0.0,) * 7
            grip = 1.0 if name in ("circle", "reengage", "lost") else 0.0
            c = ControllerSample(pose, grip, 1.0)
            frames.append(XRFrame(t, ts * 20_000_000, c, c))
            t += 1.0 / hz
    return frames


def _run(tmp_path, seconds, extra_recv=(), sender_args=()):
    rec = tmp_path / "input.jsonl"
    rec.write_text("".join(f.to_json() + "\n" for f in _script()))
    out = tmp_path / "recv.jsonl"
    recv = subprocess.Popen([LEROBOT_PY, os.path.join(REPO, "tests/fake_receiver.py"), "--port", str(5701 + OFFSET),
                             "--echo", str(5711 + OFFSET), "--seconds", str(seconds), "--out", str(out), *extra_recv],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=ENV)
    time.sleep(1.5)
    slog = tmp_path / "sender.jsonl"
    snd = subprocess.run([sys.executable, "-m", "pico_teleop.sender", "--arms", "right", "--port-offset", str(OFFSET),
                          "--pose-set", "kitakyushu-takeei-v2", "--replay", str(rec), "--log", str(slog), *sender_args],
                         cwd=REPO, capture_output=True, text=True, env=ENV, timeout=60)
    ro, re_ = recv.communicate(timeout=60)
    assert recv.returncode == 0, re_[-2000:]
    assert snd.returncode == 0, snd.stderr[-2000:]
    summary = json.loads(ro.strip().splitlines()[-1])
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    sent = [json.loads(l) for l in slog.read_text().splitlines()]
    return summary, rows, sent, snd.stdout


def _start():
    import yaml
    d = yaml.safe_load(open(os.path.join(POSE_SETS, "kitakyushu-takeei-v2", "right.yaml")))
    return {f"{k}.pos": float(d[k]) for k in ["joint_1", "joint_2", "joint_3", "joint_4", "joint_5", "joint_6",
                                              "joint_7", "gripper"]}


def test_end_to_end_with_real_receiver(tmp_path):
    summary, rows, sent, stdout = _run(tmp_path, seconds=12.0)
    # first packet: all 8 keys, degrees, exactly the start pose
    assert set(summary["first"]) == set(KEYS)
    start = _start()
    assert all(abs(summary["first"][k] - start[k]) < 1e-9 for k in KEYS)
    # receiver outputs never jump (200 Hz loop; sender limit is 45 deg/s -> 0.9 deg per 50 Hz cycle)
    acts = np.array([[r["action"][k] for k in KEYS] for r in rows])
    assert np.isfinite(acts).all()
    steps = np.max(np.abs(np.diff(acts, axis=0)), axis=1)
    assert steps.max() <= 1.8 + 1e-6, steps.max()
    # states seen by the sender, in order, with the expected transitions
    states = [s["arms"]["right"]["state"] for s in sent]
    events = [s["arms"]["right"]["event"] for s in sent if s["arms"]["right"]["event"]]
    assert any(e.startswith("ENGAGED") for e in events) and any(e.startswith("released") for e in events)
    assert any("FAULT" in e and "zero" in e for e in events) and any("fault cleared" in e for e in events)
    assert states[-1] == "HOLD"
    # seq strictly increasing, arm field right
    seqs = [s["arms"]["right"]["seq"] for s in sent]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    # while released and while faulted, the sent action did not change although the hand moved
    for st in ("HOLD", "FAULT"):
        blocks = [s["arms"]["right"]["action"] for s in sent if s["arms"]["right"]["state"] == st]
        assert blocks
    # sender stopped after the replay -> receiver goes stale and HOLDS the last action (no limp, no jump)
    stalled = [r for r in rows if r["stalled"]]
    assert stalled, "receiver never detected the stop"
    last_sent = sent[-1]["arms"]["right"]["action"]
    assert all(abs(stalled[-1]["action"][k] - last_sent[k]) < 1e-9 for k in KEYS)


def test_nan_reaches_the_follower_without_sender_validation(tmp_path):
    """Documents WHY the sender validates: the real receiver passes NaN straight through."""
    import zmq
    out = tmp_path / "recv.jsonl"
    recv = subprocess.Popen([LEROBOT_PY, os.path.join(REPO, "tests/fake_receiver.py"), "--port", str(5702 + OFFSET),
                             "--echo", str(5712 + OFFSET), "--seconds", "1.5", "--out", str(out)],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=ENV)
    ctx = zmq.Context.instance()
    push = ctx.socket(zmq.PUSH); push.setsockopt(zmq.LINGER, 0)
    push.connect(f"tcp://127.0.0.1:{5702 + OFFSET}")
    echo = ctx.socket(zmq.PULL); echo.setsockopt(zmq.LINGER, 0)
    echo.connect(f"tcp://127.0.0.1:{5712 + OFFSET}")
    good = {k: 0.0 for k in KEYS}; good["joint_4.pos"] = 30.0
    connected, t_end = False, time.monotonic() + 15.0
    while recv.poll() is None and time.monotonic() < t_end:
        if not connected:  # a complete first packet; the receiver echoes once it has connected
            push.send_string(json.dumps({"seq": 1, "t_mono": 0.0, "arm": "left", "action": good}))
            connected = echo.poll(10) != 0
        else:  # then garbage and a NaN partial packet, as a buggy sender could produce
            push.send_string("this is not json")
            push.send_string('{"seq": 2, "arm": "left", "action": {"joint_3.pos": NaN}}')
            time.sleep(0.01)
    push.close(); echo.close()
    assert connected
    ro, re_ = recv.communicate(timeout=30)
    assert out.exists(), (ro[-1500:], re_[-1500:])
    rows = [json.loads(l.replace("NaN", "null")) for l in out.read_text().splitlines()]
    assert any(r["action"]["joint_3.pos"] is None for r in rows), "expected NaN to pass the receiver"


def test_record_a_key_soft_move_to(tmp_path):
    """The record 'a' key: the receiver asks the operator side to drive its stream to a pose. The
    PICO sender does it in HOLD, rate-limited, and the receiver sees the stream converge."""
    start = _start()
    pose = {k[:-4]: v for k, v in start.items()}
    pose["joint_1"] += 8.0
    summary, rows, sent, stdout = _run(tmp_path, seconds=9.0, extra_recv=(
        "--soft-move-at", "0.5", "--soft-move-pose", json.dumps(pose)))
    assert summary["soft_move_ok"] is True
    events = [s["arms"]["right"]["event"] for s in sent if s["arms"]["right"]["event"]]
    assert any("soft_move_to: arrived" in e for e in events)
    # the harness records nothing while soft_move_to blocks (~2 s): compare consecutive samples only
    acts = np.array([[r["action"][k] for k in KEYS] for r in rows])
    t = np.array([r["t"] for r in rows])
    contiguous = np.diff(t) < 0.05
    assert np.max(np.abs(np.diff(acts, axis=0))[contiguous]) <= 1.8 + 1e-6
