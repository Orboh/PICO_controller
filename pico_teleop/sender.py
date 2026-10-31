"""PICO sender: one SDK reader -> per-arm ArmTeleop -> ActionValidator -> ZMQ to openarm_leader_remote.

Replaces `leader_agent` on the operator side; same packet, ports and socket options:
  {"seq", "t_mono", "arm", "action": {joint_1.pos..joint_7.pos, gripper.pos}}  PUSH CONFLATE=1 LINGER=0
  right -> tcp://<host>:5701 (echo 5711), left -> 5702 (echo 5712).
Never run it together with a leader_agent on the same arm port.

    bash scripts/pico_env.sh python -m pico_teleop.sender --dry-run --arms right       # print only
    bash scripts/pico_env.sh python -m pico_teleop.sender --host <follower-ip> --arms right \
        --pose-set kitakyushu-takeei-v2 --confirm-follower-at-start-pose

Start pose anchor (log-006): the first packet is the pose set's start pose. The follower aligns to
the same pose set on connect, so they match ONLY if both use the same pose set and the follower was
(re)started before this sender. There is no follower feedback on this path yet.
Stopping (Ctrl-C) makes the receiver HOLD the last target after 250 ms; it never goes limp.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from .controller import ENGAGED, ArmTeleop, PicoAdapter, TeleopConfig
from .kinematics import DEFAULT_TCP_OFFSET_M, DEFAULT_URDF, ArmKinematics
from .poses import POSE_SETS, current_pose_set, load_start_pose
from .validate import ECHO_PORTS, FOLLOWER_LIMITS_DEG, PORTS, ActionValidator, ValidatorConfig
from .xr_input import FreshnessTracker, ReplaySource, SdkSource

class Link:
    """ZMQ PUSH (commands) + PULL (echo / soft_move_to) for one arm, like leader_agent."""

    def __init__(self, host: str, port: int, echo_port: int):
        import zmq

        self.zmq = zmq
        self.ctx = zmq.Context.instance()
        self.push = self.ctx.socket(zmq.PUSH)
        self.push.setsockopt(zmq.CONFLATE, 1)
        self.push.setsockopt(zmq.LINGER, 0)
        self.push.connect(f"tcp://{host}:{port}")
        self.echo = self.ctx.socket(zmq.PULL)
        self.echo.setsockopt(zmq.CONFLATE, 1)
        self.echo.connect(f"tcp://{host}:{echo_port}")
        self.last_echo_t: float | None = None
        self.follower: dict[str, float] | None = None   # measured joints, if the receiver sends them
        self.follower_t: float | None = None
        self.sent = self.dropped = 0

    def send(self, msg: dict) -> None:
        try:
            self.push.send_string(json.dumps(msg), flags=self.zmq.NOBLOCK)
            self.sent += 1
        except self.zmq.Again:
            self.dropped += 1

    def poll(self) -> dict | None:
        """Drain echoes; return a soft_move_to command if one arrived."""
        cmd = None
        while True:
            try:
                e = json.loads(self.echo.recv_string(self.zmq.NOBLOCK))
            except self.zmq.Again:
                return cmd
            except (ValueError, TypeError):
                continue
            self.last_echo_t = time.monotonic()
            if isinstance(e.get("follower"), dict):  # proposals/lerobot-follower-feedback.patch
                self.follower, self.follower_t = e["follower"], self.last_echo_t
            if e.get("cmd") == "soft_move_to":
                cmd = e

    def close(self) -> None:
        self.push.close()
        self.echo.close()


class ArmChannel:
    """Controller + validator + link for one arm. The validator sees every packet before it leaves."""

    def __init__(self, side: str, start: dict, cfg: TeleopConfig, link: Link | None, urdf: str,
                 link_timeout_s: float, tcp_offset_m: float, max_track_err_deg: float = 8.0):
        self.side, self.link, self.link_timeout_s = side, link, link_timeout_s
        self.max_track_err_deg = max_track_err_deg  # safety protocol §1 L0: |measured - commanded| > 8 deg
        kin = ArmKinematics(side, urdf, limits_deg=FOLLOWER_LIMITS_DEG[side], margin_deg=cfg.joint_margin_deg,
                            tcp_offset_m=tcp_offset_m)
        self.arm = ArmTeleop(side, kin, start, cfg)
        self.adapter = PicoAdapter(cfg.operator_yaw_deg)
        self.val = ActionValidator(side, cfg.hz, ValidatorConfig(margin_deg=cfg.joint_margin_deg))
        first = self.val.check(self.arm.action(), None, side)
        if not first.ok:
            raise ValueError(f"{side}: start pose fails validation: {first.reasons}")
        self.last_sent = self.arm.action()
        self.seq = 0
        self.move: dict | None = None
        self.last_move_id = None

    def link_ok(self, now: float) -> bool:
        if self.link is None or self.link.last_echo_t is None:
            return True  # dry run, or the receiver has not echoed yet (it echoes only after connect)
        return now - self.link.last_echo_t <= self.link_timeout_s

    def follower_error(self, now: float) -> tuple[float, str] | None:
        """Largest |measured - last sent| over the 7 joints, if fresh feedback exists (else None)."""
        if self.link is None or self.link.follower is None or now - self.link.follower_t > 0.5:
            return None
        errs = {k: abs(float(self.link.follower[k]) - self.last_sent[k])
                for k in self.last_sent if k != "gripper.pos" and k in self.link.follower}
        if len(errs) < 7:
            return None
        k = max(errs, key=errs.get)
        return errs[k], k

    def tick(self, ctrl, fresh: bool, stream_state: str, now: float) -> tuple[dict, str | None]:
        if self.link is not None:
            cmd = self.link.poll()
            if cmd is not None and cmd.get("cmd_id") != self.last_move_id:
                self.last_move_id = cmd.get("cmd_id")
                self.move = cmd
        event = None
        lk = self.link_ok(now)
        if self.move is not None and self.arm.state != ENGAGED:
            pose = {f"{k}.pos" if not k.endswith(".pos") else k: v for k, v in self.move["pose"].items()}
            if self.arm.move_toward(pose, max_dps=self.arm.cfg.max_joint_speed_dps):
                event, self.move = "soft_move_to: arrived at the requested pose", None
        else:
            # A lost link counts as stale input: ENGAGED -> FAULT, and no new engagement until it is back.
            why = "link lost: no echo from the receiver" if not lk else f"input {stream_state}"
            fe = self.follower_error(now)
            if fe is not None and fe[0] > self.max_track_err_deg:
                # Follower is not where we command it: never engage, and stop following.
                why, lk = f"follower {fe[1]} is {fe[0]:.1f} deg from the command (> {self.max_track_err_deg})", False
            event = self.arm.step(self.adapter(ctrl, fresh and lk, why)).event
        action = self.arm.action()
        v = self.val.check(action, self.last_sent, self.side)
        if not v.ok:
            self.arm.reset_to(self.last_sent)
            event = self.arm.force_fault("validator: " + "; ".join(v.reasons)).event or \
                "validator rejected a HOLD-state packet: " + "; ".join(v.reasons)
            action = self.last_sent
        self.seq += 1
        if self.link is not None:
            self.link.send({"seq": self.seq, "t_mono": now, "arm": self.side, "action": action})
        self.last_sent = action
        return action, event


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--arms", choices=["right", "left", "both"], default="right")
    ap.add_argument("--pose-set", default=None, help="name under --pose-sets-dir (default: its CURRENT)")
    ap.add_argument("--pose-sets-dir", default=POSE_SETS)
    ap.add_argument("--urdf", default=DEFAULT_URDF)
    ap.add_argument("--tcp-offset-m", type=float, default=DEFAULT_TCP_OFFSET_M,
                    help="link7 -> grasp point along the gripper axis (0.1025 = finger root; measure it)")
    ap.add_argument("--hz", type=float, default=50.0)
    ap.add_argument("--scale", type=float, default=0.5)
    ap.add_argument("--port-offset", type=int, default=0, help="tests only: shift 5701/5711 etc.")
    ap.add_argument("--link-timeout-s", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true", help="compute and print, send nothing")
    ap.add_argument("--replay", default=None, help="drive from a gate-1 recording instead of the PICO")
    ap.add_argument("--duration-s", type=float, default=None)
    ap.add_argument("--log", default=None, help="per-cycle JSONL (raw input + output); default runs/sender-<time>.jsonl")
    ap.add_argument("--confirm-follower-at-start-pose", action="store_true",
                    help="required to send to a non-localhost host (see the start-pose anchor note)")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    local = a.host in ("127.0.0.1", "localhost")
    if not a.dry_run and not local and not a.confirm_follower_at_start_pose:
        print("[sender] REFUSED: to drive a follower on another host, confirm it was started with the SAME "
              "pose set after this pose set was chosen: --confirm-follower-at-start-pose", file=sys.stderr)
        return 2
    if a.replay and not a.dry_run and not local:
        print("[sender] REFUSED: a recording may only drive localhost or a dry run", file=sys.stderr)
        return 2
    pose_set = a.pose_set or current_pose_set(a.pose_sets_dir)
    sides = ["left", "right"] if a.arms == "both" else [a.arms]
    cfg = TeleopConfig(hz=a.hz, scale=a.scale)
    chans = []
    for s in sides:
        link = None if a.dry_run else Link(a.host, PORTS[s] + a.port_offset, ECHO_PORTS[s] + a.port_offset)
        start = load_start_pose(s, pose_set, a.pose_sets_dir)
        chans.append(ArmChannel(s, start, cfg, link, a.urdf, a.link_timeout_s, a.tcp_offset_m))
        ch = chans[-1]
        assert PORTS[s] == ch.val.port
        print(f"[sender] {s}: port {PORTS[s] + a.port_offset}, limits (deg, margin incl.) "
              f"{ {k: tuple(round(x, 1) for x in v) for k, v in ch.val.limits.items()} }", flush=True)
    src = ReplaySource(a.replay) if a.replay else SdkSource()
    tr = FreshnessTracker()
    log_path = a.log or time.strftime("runs/sender-%Y%m%d-%H%M%S.jsonl")
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    log = open(log_path, "w")
    print(f"[sender] pose_set={pose_set} arms={sides} host={a.host} hz={a.hz} scale={a.scale} "
          f"dry_run={a.dry_run} log={log_path}\n[sender] squeeze GRIP to follow, release to hold; "
          f"TRIGGER closes the gripper. Ctrl-C stops (receiver holds the last pose).", flush=True)
    period, t_start, last_print = 1.0 / a.hz, time.monotonic(), 0.0
    try:
        while a.duration_s is None or time.monotonic() - t_start < a.duration_s:
            t0 = time.monotonic()
            fr = src.read()
            tr.update(fr)
            rec = {"t": t0, "frame": json.loads(fr.to_json()), "arms": {}}
            for ch in chans:
                act, ev = ch.tick(fr.controller(ch.side), tr.fresh(ch.side), f"{tr.stream}/{tr.side_state[ch.side]}", t0)
                rec["arms"][ch.side] = {"state": ch.arm.state, "action": act, "seq": ch.seq, "event": ev}
                if ev:
                    print(f"[{ch.side}] {ev}", flush=True)
            log.write(json.dumps(rec) + "\n")
            if t0 - last_print >= 1.0:
                last_print = t0
                for ch in chans:
                    st = " ".join(f"{v:+6.1f}" for v in ch.last_sent.values())
                    print(f"[{ch.side:5s}] {ch.arm.state:7s} input={tr.stream}/{tr.side_state[ch.side]} {st}", flush=True)
            if a.replay and src.exhausted:
                break
            time.sleep(max(period - (time.monotonic() - t0), 0.0))
    except KeyboardInterrupt:
        print("[sender] Ctrl-C: stopping; the receiver will hold the last pose", flush=True)
    finally:
        src.close()
        log.close()
        for ch in chans:
            if ch.link:
                print(f"[{ch.side}] sent={ch.link.sent} dropped={ch.link.dropped}")
                ch.link.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
