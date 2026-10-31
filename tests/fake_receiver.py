"""Gate 3 helper: the REAL lerobot `OpenArmLeaderRemote` on localhost, driven as lerobot-teleoperate
would (connect, then get_action in a loop), recording every output instead of moving a robot.

Run with the lerobot venv (python 3.13), not this repo's venv:
    ~/projects/lerobot/.venv/bin/python tests/fake_receiver.py --port 25701 --echo 25711 \
        --seconds 10 --out runs/fake-recv.jsonl [--soft-move-at 6.0]
Prints a JSON summary on the last line.
"""
import argparse
import json
import logging
import time

from lerobot.teleoperators.openarm_leader_remote import OpenArmLeaderRemote, OpenArmLeaderRemoteConfig

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, required=True)
ap.add_argument("--echo", type=int, required=True)
ap.add_argument("--seconds", type=float, default=10.0)
ap.add_argument("--fps", type=float, default=200.0)
ap.add_argument("--out", required=True)
ap.add_argument("--soft-move-at", type=float, default=None)
ap.add_argument("--soft-move-pose", default=None, help="JSON {joint_1: deg, ...}")
a = ap.parse_args()
logging.basicConfig(level=logging.WARNING)

t = OpenArmLeaderRemote(OpenArmLeaderRemoteConfig(id="fake_recv", zmq_port=a.port, echo_port=a.echo,
                                                 first_packet_timeout_s=15.0))
t0 = time.monotonic()
t.connect()
summary = {"connect_s": round(time.monotonic() - t0, 3), "first": dict(t._target)}
moved = None
with open(a.out, "w") as f:
    t1 = time.monotonic()
    while time.monotonic() - t1 < a.seconds:
        now = time.monotonic()
        if a.soft_move_at is not None and moved is None and now - t1 >= a.soft_move_at:
            moved = t.soft_move_to(json.loads(a.soft_move_pose), duration_s=2.0)
        act = t.get_action()
        # stamp AFTER get_action: soft_move_to above blocks, and that gap must show in the record
        f.write(json.dumps({"t": time.monotonic() - t1, "stalled": t._stalled, "action": act}) + "\n")
        time.sleep(max(1.0 / a.fps - (time.monotonic() - now), 0.0))
summary["soft_move_ok"] = moved
t.disconnect()
print(json.dumps(summary))
