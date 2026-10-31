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
from collections import deque
import os
import sys
import time

import numpy as np
import pinocchio as pin

from .controller import ENGAGED, HOLD, ArmTeleop, PicoAdapter, TeleopConfig
from .kinematics import DEFAULT_TCP_OFFSET_M, DEFAULT_URDF, ArmKinematics
from .poses import POSE_SETS, current_pose_set, load_start_pose, pose_file, pose_file_sha
from .validate import ECHO_PORTS, FOLLOWER_LIMITS_DEG, PORTS, ActionValidator, ValidatorConfig
from .frames import R_ROBOT_FROM_XR, quat_xyzw_to_mat
from .xr_input import FreshnessTracker, ReplaySource, SdkSource, pose_status

def headset_facing_deg(headset_pose) -> float | None:
    """Headset forward (-z) in the robot frame -> yaw from robot +x in degrees (+ = left); None if no pose."""
    if pose_status(headset_pose) != "ok":
        return None
    f = R_ROBOT_FROM_XR @ (-quat_xyzw_to_mat(headset_pose[3:])[:, 2])
    return float(np.degrees(np.arctan2(f[1], f[0])))


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
        self.sent = self.dropped = self.bad_echo = self.foreign_echo = 0
        self.recent_t: deque[float] = deque(maxlen=1000)  # t_mono of our last ~20 s of packets

    def send(self, msg: dict) -> None:
        self.recent_t.append(msg["t_mono"])
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
            if "seq" in e:  # a stream echo: it must echo one of OUR packets (else: a second sender)
                if not (_finite(e.get("t_mono")) and float(e["t_mono"]) in self.recent_t):
                    self.foreign_echo += 1
                    continue
                self.rtt_ms = (now - float(e["t_mono"])) * 1000.0
            if self.last_echo_t is None or now - self.last_echo_t > gap_s:
                self.first_echo_t = now
            self.last_echo_t = now
            fol = e.get("follower")  # proposals/lerobot-follower-feedback.patch
            if isinstance(fol, dict) and all(_finite(fol.get(f"joint_{i}.pos")) for i in range(1, 8)):
                self.follower, self.follower_t = {k: float(v) for k, v in fol.items() if _finite(v)}, now
            if e.get("cmd") == "soft_move_to":
                pose = e.get("pose")
                dur = e.get("duration_s", 2.0)
                if isinstance(pose, dict) and all(_finite(pose.get(f"joint_{i}")) for i in range(1, 8)) \
                        and (pose.get("gripper") is None or _finite(pose.get("gripper"))) \
                        and _finite(dur) and 0.0 < float(dur) <= 30.0:
                    cmd = e
                else:
                    self.bad_echo += 1

    def close(self) -> None:
        self.push.close()
        self.echo.close()


class ArmChannel:
    """Controller + validator + link for one arm. The validator sees every packet before it leaves."""

    def __init__(self, side: str, start: dict, cfg: TeleopConfig, link: Link | None, urdf: str,
                 link_timeout_s: float, tcp_offset_m: float, max_track_err_deg: float = 8.0,
                 max_link_pause_s: float = 5.0, max_facing_deg: float = 75.0):
        self.side, self.link, self.link_timeout_s = side, link, link_timeout_s
        self.max_track_err_deg = max_track_err_deg  # safety protocol §1 L0: |measured - commanded| > 8 deg
        # Keep sending the HOLD target through a receiver pause (episode save) only this long. After
        # that, stop for good: a restarted receiver would take our held pose as its first target with
        # no step check (Elephant m5 #1). 5 s is a guess until record pauses are measured on the
        # follower PC; the real fix is a receiver instance id in the echo (proposal patch).
        self.max_link_pause_s = max_link_pause_s
        # The XR "forward" is fixed at recenter; an operator whose BODY is turned away maps hand motion onto
        # the wrong robot axes (headset at -100 deg turned a twist into a tilt, log-016). Head turns to watch
        # the arm (+45..58 deg, log-020) are harmless, so only the start of a squeeze is checked.
        self.max_facing_deg = max_facing_deg
        self.stopped = False
        self.foreign = False          # latched: another sender's echoes were seen on this port
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
        self.link_down = False          # receiver silent > link_timeout_s (we keep sending HOLD)
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

    def tick(self, ctrl, fresh: bool, stream_state: str, now: float,
             t_device: float | None = None, facing_deg: float | None = None) -> tuple[dict, str | None]:
        """One cycle: always sends one validated packet (the HOLD target when nothing may move).

        A silent receiver (> link_timeout_s without echo) = FAULT + no engagement, but we keep
        sending: lerobot-record stops calling get_action (no echoes) while it saves an episode or
        waits at the start-pose gate (Elephant m4 #6). Following resumes only after a release, a
        new squeeze and 0.5 s of fresh echoes. A receiver RESTART cannot be told apart from such a
        pause without follower feedback: runbook R2 (restart the sender too) covers it.
        """
        event = None
        if self.link is not None:
            nf = self.link.foreign_echo
            cmd = self.link.poll(self.link_timeout_s)
            if self.link.foreign_echo > nf and not self.foreign:
                self.foreign = True
                event = (self.arm.force_fault("foreign echo").event or "") + " | echo of packets we did not " \
                    "send: ANOTHER SENDER on this port. Engaging is locked until restart (spotter: e-stop)"
            if cmd is not None and cmd.get("cmd_id") != self.last_move_id:
                event = self._accept_move(cmd, now)
                if event.startswith("soft_move_to: moving"):
                    self.last_move_id = cmd.get("cmd_id")  # only an ACCEPTED request is deduplicated
                    self.arm.reset_squeeze()
        ls = self.link_state(now)
        if self.stopped:
            return None, None
        if ls == "lost" and self.link.last_echo_t is not None and now - self.link.last_echo_t > self.max_link_pause_s:
            self.stopped = True
            return None, (f"link down > {self.max_link_pause_s:.0f} s: SENDING STOPPED (a restarted follower "
                          "must not get our held pose). Restart the follower, then the sender (runbook R2)")
        if ls == "lost" and not self.link_down:
            self.link_down = True
            event = (self.arm.force_fault("link lost: no echo from the receiver").event or "") + \
                " | LINK DOWN: holding; if the follower was restarted, restart this sender too (runbook R2)"
        elif ls == "ok" and self.link_down:
            self.link_down, event = False, "link back: release the grip, then squeeze to continue"
        if self.move is not None and (self.arm.state != HOLD or now > self.move_deadline):
            self.move, event = None, "soft_move_to cancelled (left HOLD or expired)"
        if self.move is not None:
            if self.arm.move_toward(self.move, max_dps=self.arm.cfg.max_joint_speed_dps):
                event, self.move = "soft_move_to: arrived at the requested pose", None
        else:
            ok = ls in ("ok", "dry") and not self.foreign
            why = {"waiting": "waiting for the receiver (no echo yet: follower not ready)",
                   "lost": "link down: no echo from the receiver"}.get(ls, f"input {stream_state}")
            if self.foreign:
                why = "another sender on this port (locked until restart)"
            # Facing is checked only when STARTING (HOLD -> engage): the head may then turn to watch the arm,
            # because the motion comes from the controller's position, not the head (log-020/021).
            if ok and self.arm.state != ENGAGED and facing_deg is not None and abs(facing_deg) > self.max_facing_deg:
                ok, why = False, (f"operator faces {facing_deg:+.0f} deg from the robot's forward "
                                  f"(> {self.max_facing_deg:.0f}) when starting: face forward or recenter the headset")
            if not ok:  # following is not allowed, but a FAULT may still be cleared by a real release
                cleared = self.arm.clear_fault_on_release(self.adapter(ctrl, fresh, why, t_device))
                event = " | ".join(e for e in (event, cleared) if e) or None
            fe = self.follower_error(now)
            if fe is not None and fe[0] > self.max_track_err_deg:
                ok, why = False, f"follower {fe[1]} is {fe[0]:.1f} deg from the command (> {self.max_track_err_deg})"
            h = self.adapter(ctrl, fresh and ok, why, t_device)
            out = self.arm.step(h)
            event = " | ".join(e for e in (event, out.event) if e) or None
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
    ap.add_argument("--orientation", action="store_true",
                    help="wrist rotation: the hand's rotation since engage also turns the gripper (log-015)")
    ap.add_argument("--hz", type=float, default=50.0)
    ap.add_argument("--scale", type=float, default=0.5)
    ap.add_argument("--port-offset", type=int, default=0, help="tests only: shift 5701/5711 etc.")
    ap.add_argument("--link-timeout-s", type=float, default=1.0)
    ap.add_argument("--stale-s", type=float, default=0.4,
                    help="input older than this = stale (FAULT while engaged); 0.4 s from live PICO data")
    ap.add_argument("--max-facing-deg", type=float, default=75.0,
                    help="refuse to START (engage) when the headset faces further than this from the robot's "
                         "forward; no FAULT while engaged (the head may turn to watch the arm)")
    ap.add_argument("--max-link-pause-s", type=float, default=5.0,
                    help="after this long without echo, stop sending for good (runbook R2)")
    ap.add_argument("--dry-run", action="store_true", help="compute and print, send nothing")
    ap.add_argument("--replay", default=None, help="drive from a gate-1 recording instead of the PICO")
    ap.add_argument("--duration-s", type=float, default=None)
    ap.add_argument("--log", default=None, help="per-cycle JSONL (raw input + output); default runs/sender-<time>.jsonl")
    ap.add_argument("--confirm-follower-at-start-pose", action="store_true",
                    help="required to send to a non-localhost host (see the start-pose anchor note)")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    a = parse_args(argv)
    if a.port_offset != 0 and a.port_offset < 10000:  # e.g. +1 would turn right 5701 into LEFT 5702
        print("[sender] REFUSED: --port-offset must be 0 (real ports) or >= 10000 (tests)", file=sys.stderr)
        return 2
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
    cfg = TeleopConfig(hz=a.hz, scale=a.scale, orientation=a.orientation)
    chans = []
    for s in sides:
        link = None if a.dry_run else Link(a.host, PORTS[s] + a.port_offset, ECHO_PORTS[s] + a.port_offset)
        start = load_start_pose(s, pose_set, a.pose_sets_dir)
        chans.append(ArmChannel(s, start, cfg, link, a.urdf, a.link_timeout_s, a.tcp_offset_m,
                                max_link_pause_s=a.max_link_pause_s, max_facing_deg=a.max_facing_deg))
        ch = chans[-1]
        print(f"[sender] {s}: tcp://{a.host}:{PORTS[s] + a.port_offset} (echo {ECHO_PORTS[s] + a.port_offset}), "
              f"start pose {pose_file(s, pose_set, a.pose_sets_dir)} sha256 {pose_file_sha(s, pose_set, a.pose_sets_dir)} "
              "(compare with the follower PC: sha256sum of its CURRENT set's file), limits (deg, margin incl.) "
              f"{ {k: tuple(round(x, 1) for x in v) for k, v in ch.val.limits.items()} }", flush=True)
    src = ReplaySource(a.replay) if a.replay else SdkSource()
    # 0.4 s (Suraj, 2026-10-31, log-013): the PICO stalls 0.22-0.33 s about every 15 s (headset/app
    # side; the link itself pings fine). While input is stale the target does not advance; when data
    # returns, the 10 cm one-update hand-step FAULT and the rate limits catch any jump. diag uses 0.1 s.
    tr = FreshnessTracker(stale_s=a.stale_s)
    log_path = a.log or time.strftime("runs/sender-%Y%m%d-%H%M%S.jsonl")
    os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
    log = open(log_path, "w")
    session = uuid.uuid4().hex[:8]
    log.write(json.dumps({"session": session, "pose_set": pose_set, "args": vars(a), "config": vars(cfg),
                          "pose_sha": {s: pose_file_sha(s, pose_set, a.pose_sets_dir) for s in sides}}) + "\n")
    print(f"[sender] pose_set={pose_set} arms={sides} host={a.host} hz={a.hz} scale={a.scale} "
          f"orientation={'ON' if a.orientation else 'off'} "
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
                act, ev = ch.tick(fr.controller(ch.side), tr.fresh(ch.side), f"{tr.stream}/{tr.side_state[ch.side]}",
                                  t0, fr.t_device_ns / 1e9 if fr.t_device_ns else None,
                                  headset_facing_deg(fr.headset))
                rec["arms"][ch.side] = {"state": "STOPPED" if ch.stopped else ch.arm.state, "action": act, "seq": ch.seq, "event": ev,
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
                    rot = ""
                    if cfg.orientation and ch.arm.anchor_tcp_rot is not None:
                        # gripper rotation since engage, as a rotation vector in the robot frame [deg]
                        w = np.degrees(pin.log3(ch.arm.kin.fk(ch.arm.q).rotation @ ch.arm.anchor_tcp_rot.T))
                        rot = f" rot x{w[0]:+5.0f} y{w[1]:+5.0f} z{w[2]:+5.0f} deg"
                    lk = "SENDING STOPPED " if ch.stopped else ("LINK DOWN " if ch.link_down else "")
                    fy = headset_facing_deg(fr.headset)
                    if fy is not None:
                        lk += f"facing {fy:+.0f}deg" + (" (TURNED: check your body faces forward) "
                                                         if abs(fy) > ch.max_facing_deg else " ")
                    if not fr.t_device_ns:
                        lk += "NO PICO DATA: turn sending on in the app "
                    print(f"[{ch.side:5s}] {lk}{ch.arm.state:7s} input={tr.stream}/{tr.side_state[ch.side]} {st}  "
                          f"TCP moved x{d[0]:+5.1f} y{d[1]:+5.1f} z{d[2]:+5.1f} cm{rot}", flush=True)
            if chans and all(ch.stopped for ch in chans):
                print("[sender] sending stopped on every arm: exiting", flush=True)
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
                      f"bad_echo={ch.link.bad_echo} foreign_echo={ch.link.foreign_echo}")
                ch.link.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
