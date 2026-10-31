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
import math
import uuid
import os
import sys
import time

from .controller import ENGAGED, HOLD, ArmTeleop, PicoAdapter, TeleopConfig
from .kinematics import DEFAULT_TCP_OFFSET_M, DEFAULT_URDF, ArmKinematics
from .poses import POSE_SETS, current_pose_set, load_start_pose, pose_file, pose_file_sha
from .validate import ECHO_PORTS, FOLLOWER_LIMITS_DEG, PORTS, ActionValidator, ValidatorConfig
from .xr_input import FreshnessTracker, ReplaySource, SdkSource

def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


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
        self.first_echo_t: float | None = None          # start of the current continuous echo stream
        self.follower: dict[str, float] | None = None   # measured joints, if the receiver sends them
        self.follower_t: float | None = None
        self.rtt_ms: float | None = None
        self.sent = self.dropped = self.bad_echo = 0

    def send(self, msg: dict) -> None:
        try:
            self.push.send_string(json.dumps(msg), flags=self.zmq.NOBLOCK)
            self.sent += 1
        except self.zmq.Again:
            self.dropped += 1

    def poll(self, gap_s: float) -> dict | None:
        """Drain echoes (schema-checked; bad ones are counted and dropped); return a soft_move_to
        command if one arrived. `gap_s`: a silence longer than this restarts `first_echo_t`."""
        cmd = None
        while True:
            try:
                e = json.loads(self.echo.recv_string(self.zmq.NOBLOCK))
            except self.zmq.Again:
                return cmd
            except (ValueError, TypeError):
                self.bad_echo += 1
                continue
            if not isinstance(e, dict):
                self.bad_echo += 1
                continue
            now = time.monotonic()
            if self.last_echo_t is None or now - self.last_echo_t > gap_s:
                self.first_echo_t = now
            self.last_echo_t = now
            if _finite(e.get("t_mono")):
                self.rtt_ms = (now - float(e["t_mono"])) * 1000.0
            fol = e.get("follower")  # proposals/lerobot-follower-feedback.patch
            if isinstance(fol, dict) and all(_finite(fol.get(f"joint_{i}.pos")) for i in range(1, 8)):
                self.follower, self.follower_t = {k: float(v) for k, v in fol.items() if _finite(v)}, now
            if e.get("cmd") == "soft_move_to":
                pose = e.get("pose")
                if isinstance(pose, dict) and all(_finite(pose.get(f"joint_{i}")) for i in range(1, 8)) \
                        and (pose.get("gripper") is None or _finite(pose.get("gripper"))):
                    cmd = e
                else:
                    self.bad_echo += 1

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
        self.move_deadline = 0.0
        self.last_move_id = None
        self.link_down = False          # set after a confirmed link was lost: sending stops for good
        self.last_refusal_t = 0.0

    def link_state(self, now: float) -> str:
        """'dry' (no link) / 'waiting' (no echo yet, or not yet 0.5 s of echoes) / 'ok' / 'lost'."""
        if self.link is None:
            return "dry"
        if self.link.last_echo_t is None:
            return "waiting"
        if now - self.link.last_echo_t > self.link_timeout_s:
            return "lost"
        # The receiver echoes only from its main loop, i.e. after the follower finished aligning.
        return "ok" if now - self.link.first_echo_t >= 0.5 else "waiting"

    def follower_error(self, now: float) -> tuple[float, str] | None:
        """Largest |measured - last sent| over the 7 joints, if fresh feedback exists (else None)."""
        if self.link is None or self.link.follower is None or now - self.link.follower_t > 0.5:
            return None
        errs = {k: abs(self.link.follower[k] - self.last_sent[k])
                for k in self.last_sent if k != "gripper.pos" and k in self.link.follower}
        if len(errs) < 7:
            return None
        k = max(errs, key=errs.get)
        return errs[k], k

    def _accept_move(self, cmd: dict, now: float) -> str:
        """soft_move_to (record 'a' key): only in HOLD, pose checked against the validator limits."""
        if self.arm.state != HOLD:
            return f"soft_move_to ignored: arm is {self.arm.state} (only in HOLD)"
        pose = {f"{k}.pos": float(v) for k, v in cmd["pose"].items() if k.startswith("joint_") or k == "gripper"}
        pose.setdefault("gripper.pos", self.arm.gripper)
        bad = [k for k, (lo, hi) in self.val.limits.items() if not lo <= pose.get(k, math.nan) <= hi]
        if bad:
            return f"soft_move_to ignored: {bad} outside the limits"
        self.move = pose
        self.move_deadline = now + float(cmd.get("duration_s") or 2.0) + 4.0  # receiver gives up then
        return "soft_move_to: moving the HOLD target to the requested pose"

    def tick(self, ctrl, fresh: bool, stream_state: str, now: float) -> tuple[dict | None, str | None]:
        """One cycle. Returns (action sent or None if sending has stopped, event text)."""
        if self.link_down:
            return None, None
        event = None
        if self.link is not None:
            cmd = self.link.poll(self.link_timeout_s)
            if cmd is not None and cmd.get("cmd_id") != self.last_move_id:
                self.last_move_id = cmd.get("cmd_id")
                event = self._accept_move(cmd, now)
        ls = self.link_state(now)
        if ls == "lost":
            ev = self.arm.force_fault("link lost: no echo from the receiver").event
            self.link_down = True
            return None, (ev or "") + " | link lost: SENDING STOPPED. Restart the follower, then the sender (runbook R2)"
        if self.move is not None and (self.arm.state != HOLD or now > self.move_deadline):
            self.move, event = None, "soft_move_to cancelled (left HOLD or expired)"
        if self.move is not None:
            if self.arm.move_toward(self.move, max_dps=self.arm.cfg.max_joint_speed_dps):
                event, self.move = "soft_move_to: arrived at the requested pose", None
        else:
            ok = ls in ("ok", "dry")
            why = "waiting for the receiver (no echo yet: follower not ready)" if not ok else f"input {stream_state}"
            fe = self.follower_error(now)
            if fe is not None and fe[0] > self.max_track_err_deg:
                ok, why = False, f"follower {fe[1]} is {fe[0]:.1f} deg from the command (> {self.max_track_err_deg})"
            h = self.adapter(ctrl, fresh and ok, why)
            out = self.arm.step(h)
            event = out.event or event
            squeezed = ctrl.grip >= self.arm.cfg.clutch_on if _finite(ctrl.grip) else False
            if not ok and out.state == HOLD and squeezed and now - self.last_refusal_t > 2.0:
                self.last_refusal_t, event = now, f"engage refused: {why}"
        action = self.arm.action()
        v = self.val.check(action, self.last_sent, self.side)
        if not v.ok:
            self.arm.reset_to(self.last_sent)
            self.move = None
            event = self.arm.force_fault("validator: " + "; ".join(v.reasons)).event or \
                "validator rejected a packet (not sent): " + "; ".join(v.reasons)
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
    real_ports = not a.dry_run and a.port_offset == 0  # 5701/5702: a real follower may listen, any host
    if real_ports and not a.confirm_follower_at_start_pose:
        print("[sender] REFUSED: ports 5701/5702 can drive a real follower. Follow docs/hardware-runbook.md "
              "(R1-R3), then add --confirm-follower-at-start-pose", file=sys.stderr)
        return 2
    if a.replay and real_ports:
        print("[sender] REFUSED: a recording may not drive the real ports (dry run or --port-offset only)",
              file=sys.stderr)
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
        print(f"[sender] {s}: tcp://{a.host}:{PORTS[s] + a.port_offset} (echo {ECHO_PORTS[s] + a.port_offset}), "
              f"start pose {pose_file(s, pose_set, a.pose_sets_dir)} sha256 {pose_file_sha(s, pose_set, a.pose_sets_dir)} "
              "(compare with the follower PC: sha256sum of its CURRENT set's file), limits (deg, margin incl.) "
              f"{ {k: tuple(round(x, 1) for x in v) for k, v in ch.val.limits.items()} }", flush=True)
    src = ReplaySource(a.replay) if a.replay else SdkSource()
    # 0.25 s: live Wi-Fi had 25 gaps > 100 ms per minute (log-007); 0.25 s = the receiver hold threshold
    tr = FreshnessTracker(stale_s=0.25)
    log_path = a.log or time.strftime("runs/sender-%Y%m%d-%H%M%S.jsonl")
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    log = open(log_path, "w")
    session = uuid.uuid4().hex[:8]
    log.write(json.dumps({"session": session, "pose_set": pose_set, "args": vars(a), "config": vars(cfg),
                          "pose_sha": {s: pose_file_sha(s, pose_set, a.pose_sets_dir) for s in sides}}) + "\n")
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
                rec["arms"][ch.side] = {"state": ch.arm.state, "action": act, "seq": ch.seq, "event": ev,
                                        "link": ch.link_state(t0), "rtt_ms": ch.link.rtt_ms if ch.link else None,
                                        "follower": ch.link.follower if ch.link else None}
                if ev:
                    print(f"[{ch.side}] {ev}", flush=True)
            log.write(json.dumps(rec) + "\n")
            if t0 - last_print >= 1.0:
                last_print = t0
                for ch in chans:
                    st = " ".join(f"{v:+6.1f}" for v in ch.last_sent.values())
                    # TCP motion since engage, robot frame (x fwd, y left, z up): the axis-sign check
                    tcp = ch.arm.kin.fk(ch.arm.q).translation
                    d = (tcp - ch.arm.anchor_tcp) * 100 if ch.arm.anchor_tcp is not None else tcp * 0
                    print(f"[{ch.side:5s}] {ch.arm.state:7s} input={tr.stream}/{tr.side_state[ch.side]} {st}  "
                          f"TCP moved x{d[0]:+5.1f} y{d[1]:+5.1f} z{d[2]:+5.1f} cm", flush=True)
            if chans and all(ch.link_down for ch in chans):
                print("[sender] every arm's link is down: exiting", flush=True)
                break
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
                print(f"[{ch.side}] session={session} sent={ch.link.sent} dropped={ch.link.dropped} "
                      f"bad_echo={ch.link.bad_echo}")
                ch.link.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
