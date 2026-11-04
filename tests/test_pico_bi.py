"""scripts/pico_bi_leader.py と scripts/pico_bi.sh（両腕の PICO の入口）の安全に関わる振る舞い（2026-11-04・Suraj）。

実機は使わない: 受け手の代わりに PICO_BI_LEADER_CMD の短いコマンドを動かす。
"""
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
LEADER = REPO / "scripts" / "pico_bi_leader.py"
BI = REPO / "scripts" / "pico_bi.sh"


def _run_leader(cmd: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PICO_BI_LEADER_CMD=cmd, PICO_BI_LEADER_GAP_S="0.1")
    env.pop("POSE_SET", None)
    return subprocess.run([sys.executable, str(LEADER)], env=env, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, timeout=30)


def test_one_side_stops_then_the_other_gets_esc():
    """片腕の受け手が止まったら、もう片方にも esc（普通の終わり方）を送り、片腕だけ動き続けない。"""
    cmd = ('if [ {side} = left ]; then echo up; sleep 0.5; exit 0; '
           'else IFS= read -rsn1 k; printf "got %q\\n" "$k"; exit 0; fi')
    r = _run_leader(cmd)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[R] got $'\\E'" in r.stdout
    assert "もう片方にも esc" in r.stdout


def test_output_is_labelled_per_arm_and_exit_code_is_the_worst():
    """出力に [L] / [R] が付き、終了コードは悪い方を返す。"""
    r = _run_leader('echo hello-{side}; [ {side} = right ] && exit 3; exit 0')
    assert "[L] hello-left" in r.stdout and "[R] hello-right" in r.stdout
    assert r.returncode == 3


def test_leader_refuses_without_pose_set():
    """POSE_SET が無いと受け手を起こさない。"""
    env = {k: v for k, v in os.environ.items() if k not in ("POSE_SET", "PICO_BI_LEADER_CMD")}
    r = subprocess.run([sys.executable, str(LEADER)], env=env, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=10)
    assert r.returncode == 2 and "POSE_SET" in r.stderr


def test_pico_bi_refusals_move_nothing():
    """pico_bi.sh: POSE_SET・OPERATOR・知らない段・知らないセットは、何も起こさずに止まる。"""
    base = {k: v for k, v in os.environ.items() if k not in ("POSE_SET", "OPERATOR")}
    cases = [(["follower"], {}, "POSE_SET"),
             (["leader"], {"POSE_SET": "no-such-set-xyz"}, "開始姿勢セットが無い"),
             (["sender", "fly"], {"POSE_SET": "kitakyushu-takeei-v5"}, "mode"),
             (["follower", "real"], {"POSE_SET": "kitakyushu-takeei-v5", "OPERATOR": "Test"}, "stage")]
    for args, extra, word in cases:
        r = subprocess.run(["bash", str(BI), *args], env=dict(base, **extra), capture_output=True, text=True, timeout=20)
        assert r.returncode != 0, (args, r.stdout)
        assert word in r.stderr + r.stdout, (args, r.stderr)
