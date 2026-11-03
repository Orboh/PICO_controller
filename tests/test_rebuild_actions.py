"""tools/rebuild_actions.py (log-047): arm actions from the measured follower state `lead` frames ahead, gripper
kept, a new dataset version that loads in lerobot with consistent stats. Needs lerobot (skipped otherwise)."""
import json
import pathlib
import sys

import numpy as np
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import rebuild_actions as ra  # noqa: E402


def test_rebuild_episode_shifts_arm_and_keeps_gripper():
    state = np.array([[0, 0, 9], [1, 10, 9], [2, 20, 9], [3, 30, 9]], dtype=float)
    action = np.array([[5, 5, -1], [6, 6, -2], [7, 7, -3], [8, 8, -4]], dtype=float)
    out = ra.rebuild_episode(action, state, [0, 1], [0, 1], lead=2)
    assert out[:, :2].tolist() == [[2, 20], [3, 30], [3, 30], [3, 30]]   # held at the last frame
    assert out[:, 2].tolist() == [-1, -2, -3, -4]                          # gripper = command
    assert np.array_equal(ra.rebuild_episode(action, state, [0, 1], [0, 1], 0)[:, :2], state[:, :2])


def test_arm_columns_default_and_missing():
    info = {"features": {"action": {"names": ["l_joint_1.pos", "l_gripper.pos"]},
                         "observation.state": {"names": ["l_gripper.pos", "l_joint_1.pos"]}}}
    assert ra.arm_columns(info, None) == ([0], [1], ["l_joint_1.pos"])
    with pytest.raises(SystemExit):
        ra.arm_columns(info, ["l_joint_9.pos"])


def test_real_dataset_round_trip(tmp_path):
    pytest.importorskip("lerobot.datasets", reason="needs lerobot (run in the lerobot venv)")
    from lerobot.datasets import LeRobotDataset

    names = ["left_joint_1.pos", "left_joint_2.pos", "left_gripper.pos"]
    feats = {"action": {"dtype": "float32", "shape": (3,), "names": names},
             "observation.state": {"dtype": "float32", "shape": (3,), "names": names}}
    src = tmp_path / "openarm_pico_left_gate"
    ds = LeRobotDataset.create(repo_id="Orboh/t_pico", fps=30, features=feats, root=src, use_videos=False)
    for ep in range(2):
        for t in range(12):
            st = np.array([t, 10 * t + ep, -5], dtype=np.float32)
            ds.add_frame({"action": st + np.array([1, 1, 3], dtype=np.float32), "observation.state": st, "task": "x"})
        ds.save_episode()
    ds.finalize()
    before = (src / "data" / "chunk-000" / "file-000.parquet").read_bytes()

    dst = tmp_path / "openarm_pico_left_gate_measact_k3"
    assert ra.main([str(src), str(dst), "--lead", "3"]) == 0
    assert (src / "data" / "chunk-000" / "file-000.parquet").read_bytes() == before      # source untouched
    with pytest.raises(SystemExit, match="exists"):
        ra.rebuild(src, dst, 3)

    new = LeRobotDataset(repo_id="Orboh/t_pico", root=dst)
    for i in range(len(new)):
        f = new[i]
        ep, t = int(f["episode_index"]), int(f["frame_index"])
        ahead = min(t + 3, 11)
        assert f["action"].tolist() == pytest.approx([ahead, 10 * ahead + ep, -2])
    stats = json.loads((dst / "meta" / "stats.json").read_text())["action"]
    all_actions = np.stack([new[i]["action"].numpy() for i in range(len(new))])
    assert stats["max"] == pytest.approx(all_actions.max(0).tolist())
    assert stats["mean"] == pytest.approx(all_actions.mean(0).tolist(), abs=1e-4)
    import pandas as pd
    eps = pd.read_parquet(next((dst / "meta" / "episodes").rglob("*.parquet")))
    assert eps["stats/action/max"][1].tolist() == pytest.approx([11, 111, -2])
    assert eps["stats/action/count"][0].tolist() == [12]
    prov = json.loads((dst / "meta" / "rebuild_actions.json").read_text())
    assert prov["lead_frames"] == 3 and prov["kept_as_command"] == ["left_gripper.pos"] and prov["episodes"] == 2

    leader = tmp_path / "openarm_leader_left_gate"
    src.rename(leader)
    assert ra.main([str(leader), str(tmp_path / "x")]) == 2          # leader data refused without --allow-leader
