"""Gate 1: guided input-only diagnostic. Reads the PICO, records it, scores it. Sends nothing to any robot.

Run from the repo root (operator steps: README.md "Gate 1"):
    cd ~/projects/PICO-controller
    bash scripts/pico_env.sh python -m pico_teleop.diag_input            # guided, records runs/diag-<time>.jsonl
    bash scripts/pico_env.sh python -m pico_teleop.diag_input --replay runs/diag-<time>.jsonl   # re-score

Phases (Enter starts each one): left only, right only, left tracking loss, right tracking loss,
headset off. PASS needs every REQUIRED check below; menu buttons are reported but not required
(the PICO system may reserve them). Right A is never pressed: in the XRoboToolkit app A toggles
sending (XRoboToolkit-PC-Service-Pybind issue #7), which would turn the stream off mid-run. The summary lists every invalid / frozen / stale interval.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field

import numpy as np

from .xr_input import SIDES, FreshnessTracker, ReplaySource, SdkSource, XRFrame, analog_ok

PHASES = [
    ("left", 15.0, "LEFT controller only (lay the RIGHT one still on the table). Move it >= 10 cm "
                   "forward/back, left/right, up/down. Release then squeeze GRIP fully twice, TRIGGER "
                   "fully twice. Press X, then Y. Press the left menu button once if it has one."),
    ("right", 15.0, "RIGHT controller only (lay the LEFT one still). Same moves. Press B. "
                    "Do NOT press A (it toggles data sending in the XRoboToolkit app) or the Home button."),
    ("left_loss", 8.0, "Hide the LEFT controller behind your back (or cover it with both hands) for "
                       "about 4 s, then bring it back in view."),
    ("right_loss", 8.0, "Same with the RIGHT controller."),
    ("headset_off", 12.0, "Take the headset OFF and lay it down for about 6 s, then put it back on."),
]
MIN_HZ, MAX_GAP_S = 30.0, 0.25
SETTLE_S = 2.0  # live run 2026-10-31: putting the other controller down took about 2 s (log-007)  # 0.25 s = the receiver's stale_warn_ms: a longer gap makes it hold


@dataclass
class SideStats:
    pos: list = field(default_factory=list)
    grip: list = field(default_factory=list)
    trigger: list = field(default_factory=list)
    pressed: dict = field(default_factory=lambda: {"menu": 0, "primary": 0, "secondary": 0})
    status: dict = field(default_factory=dict)

    def moved(self) -> float:
        """p95 - p5 on the best axis (one outlier cannot fake a move)."""
        if len(self.pos) < 5:
            return 0.0
        p = np.asarray(self.pos)
        return float(np.max(np.percentile(p, 95, axis=0) - np.percentile(p, 5, axis=0)))

    def swept(self, name: str) -> float:
        v = getattr(self, name)
        return float(max(v) - min(v)) if v else 0.0


@dataclass
class PhaseResult:
    name: str
    updates: int = 0
    duration: float = 0.0
    max_gap: float = 0.0
    backwards: int = 0
    sides: dict = field(default_factory=lambda: {s: SideStats() for s in SIDES})
    events: list = field(default_factory=list)  # (who, state, start s, length s)

    @property
    def hz(self) -> float:
        return self.updates / self.duration if self.duration > 0 else 0.0


def score_phase(name: str, frames: list[XRFrame], t0: float, stale_s: float = 0.1) -> PhaseResult:
    r = PhaseResult(name)
    if not frames:
        return r
    tr = FreshnessTracker(stale_s=stale_s)
    last_ts, last_change, open_ev = None, frames[0].t_host, {}
    for fr in frames:
        tr.update(fr)
        if fr.t_device_ns != last_ts:
            if last_ts is not None and fr.t_device_ns != 0:
                r.updates += 1
                r.max_gap = max(r.max_gap, fr.t_host - last_change)
                r.backwards += fr.t_device_ns < last_ts
            last_ts, last_change = fr.t_device_ns, fr.t_host
        states = {"stream": tr.stream, **tr.side_state}
        for who, st in states.items():
            if st != "ok" and who not in open_ev:
                open_ev[who] = (st, fr.t_host)
            elif st == "ok" and who in open_ev:
                st0, ts = open_ev.pop(who)
                r.events.append((who, st0, round(ts - t0, 2), round(fr.t_host - ts, 2)))
        for s in SIDES:
            c, ss = fr.controller(s), r.sides[s]
            ss.status[tr.side_state[s]] = ss.status.get(tr.side_state[s], 0) + 1
            if tr.side_state[s] in ("ok", "frozen") and fr.t_host - t0 >= SETTLE_S:
                ss.pos.append(c.pose[:3])  # skip the first seconds: the operator is still setting up
            if analog_ok(c.grip) and analog_ok(c.trigger):
                ss.grip.append(c.grip)
                ss.trigger.append(c.trigger)
            for b in ss.pressed:
                ss.pressed[b] += bool(getattr(c, b))
    for who, (st0, ts) in open_ev.items():
        r.events.append((who, st0, round(ts - t0, 2), round(frames[-1].t_host - ts, 2)))
    r.max_gap = max(r.max_gap, frames[-1].t_host - last_change)
    r.duration = frames[-1].t_host - frames[0].t_host
    return r


def checks_for(r: PhaseResult, min_move=0.05, still_max=0.03, min_sweep=0.8) -> dict[str, bool]:
    c: dict[str, bool] = {}
    if r.name in SIDES:
        a, o = r.name, ("right" if r.name == "left" else "left")
        sa, so = r.sides[a], r.sides[o]
        c[f"{a}: stream >= {MIN_HZ:.0f} Hz"] = r.hz >= MIN_HZ
        c[f"{a}: no gap > {MAX_GAP_S} s"] = r.max_gap <= MAX_GAP_S
        c[f"{a}: moved >= {min_move * 100:.0f} cm"] = sa.moved() >= min_move
        c[f"{a}: {o} stayed still (< {still_max * 100:.0f} cm; sides not swapped)"] = len(so.pos) >= 5 and so.moved() < still_max
        c[f"{a}: grip swept >= {min_sweep}"] = sa.swept("grip") >= min_sweep
        c[f"{a}: trigger swept >= {min_sweep}"] = sa.swept("trigger") >= min_sweep
        if a == "left":  # right primary = A toggles sending in the app (upstream pybind issue #7): not pressed
            c[f"{a}: primary pressed"] = sa.pressed["primary"] > 0
        c[f"{a}: secondary pressed"] = sa.pressed["secondary"] > 0
    elif r.name.endswith("_loss"):
        # Evidence only (log-007): hiding a controller produced NO invalid / frozen sample on the PICO
        # 4 Ultra; the pose stays valid and nearly still. Required: the stream stayed alive.
        side = r.name.split("_")[0]
        c[f"{side} loss: stream stayed >= {MIN_HZ:.0f} Hz (loss itself is reported, not required)"] = r.hz >= MIN_HZ
    elif r.name == "headset_off":
        c["headset off: stream went stale / no data / backwards"] = any(e[0] == "stream" for e in r.events)
    return c


def format_report(results: list[PhaseResult], label: str, expected: int = len(PHASES)) -> tuple[str, bool]:
    out, ok_all = [f"===== GATE 1 ({label}) ====="], True
    for r in results:
        out.append(f"--- {r.name}: {r.duration:.1f}s, {r.updates} updates (~{r.hz:.0f} Hz), "
                   f"max gap {r.max_gap * 1000:.0f} ms, timestamp backwards {r.backwards}x")
        for s in SIDES:
            ss = r.sides[s]
            out.append(f"    {s:5s} moved {ss.moved() * 100:5.1f} cm  grip {ss.swept('grip'):.2f}  "
                       f"trigger {ss.swept('trigger'):.2f}  pressed {ss.pressed}  states {ss.status}")
        for e in r.events:
            out.append(f"    event: {e[0]} {e[1]} at +{e[2]} s for {e[3]} s")
        for name, ok in checks_for(r).items():
            ok_all &= ok
            out.append(f"    {'PASS' if ok else 'FAIL'}  {name}")
        for s in SIDES:
            if r.name == s:
                out.append(f"    INFO  {s} menu pressed: {r.sides[s].pressed['menu'] > 0} (not required)")
    ok_all &= len(results) == expected
    out.append(f"GATE 1 ({label}): {'PASS' if ok_all else 'FAIL'}")
    return "\n".join(out), ok_all


def other_pico_processes() -> list[str]:
    """Senders that could move an arm while the operator waves the controllers."""
    try:
        res = subprocess.run(["pgrep", "-af", "pico_teleop.py|leader_agent|pico_teleop.sender"],
                             capture_output=True, text=True)
    except FileNotFoundError:
        return []
    return [ln for ln in res.stdout.splitlines() if str(os.getpid()) not in ln.split()[0:1]]


def run_live(record: str, phase_seconds: dict, prompt: bool, hz: float, only: list[str] | None = None,
             lead_in_s: float = 0.0) -> list[tuple[str, list, float]]:
    src = SdkSource()
    t_wait = time.monotonic()
    while time.monotonic() - t_wait < 10.0:  # pre-check: do not run phases on a dead stream
        if src.read().t_device_ns != 0:
            break
        time.sleep(0.05)
    else:
        src.close()
        raise SystemExit("\n[diag] NO CONTROLLER DATA for 10 s (device timestamp 0). In the XRoboToolkit app: "
                         "tick Controller, then turn sending on (Send in the UI, or press A once). "
                         "Also check that no other program reads the PICO (e.g. the PC Service 3D demo).")
    if lead_in_s > 0:  # counted AFTER the data check, so the operator's schedule cannot drift
        print(f"[diag] live data OK. First phase starts in {lead_in_s:.0f} s", flush=True)
        time.sleep(lead_in_s)
    out, rec = [], open(record, "w")
    try:
        for name, dur, text in PHASES:
            if only and name not in only:
                continue
            dur = phase_seconds.get(name, dur)
            print(f"\n[{name}] {text}\n  ({dur:.0f} s)", flush=True)
            if prompt:
                input("  press Enter to start this phase ")
            frames, t0, last = [], time.monotonic(), 0.0
            while time.monotonic() - t0 < dur:
                ts = time.monotonic()
                fr = src.read()
                frames.append(fr)
                rec.write(json.dumps({"phase": name, "frame": json.loads(fr.to_json())}) + "\n")
                if ts - last >= 1.0:
                    last = ts
                    l, r = fr.left, fr.right
                    print(f"  {dur - (ts - t0):4.0f}s  ts={fr.t_device_ns}  L g={l.grip:.2f} t={l.trigger:.2f} "
                          f"xyz={[round(v, 2) for v in l.pose[:3]]}  R g={r.grip:.2f} t={r.trigger:.2f} "
                          f"xyz={[round(v, 2) for v in r.pose[:3]]}", flush=True)
                time.sleep(max(1.0 / hz - (time.monotonic() - ts), 0.0))
            out.append((name, frames, t0))
    finally:
        src.close()
        rec.close()
    return out


def load_replay(path: str) -> list[tuple[str, list, float]]:
    phases: dict[str, list] = {}
    with open(path) as f:
        for line in f:
            if line.strip():
                d = json.loads(line)
                phases.setdefault(d["phase"], []).append(XRFrame.from_json(json.dumps(d["frame"])))
    return [(n, fr, fr[0].t_host) for n, fr in phases.items()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replay", default=None, help="re-score a recording instead of reading the PICO")
    ap.add_argument("--record", default=None, help="default: runs/diag-<YYYYmmdd-HHMMSS>.jsonl")
    ap.add_argument("--no-prompt", action="store_true", help="do not wait for Enter between phases")
    ap.add_argument("--phase-seconds", type=float, default=None, help="override every phase length")
    ap.add_argument("--hz", type=float, default=200.0, help="polling rate (the SDK pushes at its own rate)")
    ap.add_argument("--phase", action="append", choices=[n for n, _, _ in PHASES],
                    help="run only this phase (repeatable); gate 1 needs every phase PASS across runs")
    ap.add_argument("--lead-in-s", type=float, default=0.0, help="countdown after the data check")
    a = ap.parse_args(argv)

    if a.replay:
        phases, label = load_replay(a.replay), f"REPLAY of {a.replay}"
    else:
        others = other_pico_processes()
        if others:
            print("[diag] REFUSED: another PICO/leader sender is running and could move an arm:\n  "
                  + "\n  ".join(others), file=sys.stderr)
            return 2
        record = a.record or time.strftime("runs/diag-%Y%m%d-%H%M%S.jsonl")
        os.makedirs(os.path.dirname(os.path.abspath(record)), exist_ok=True)
        print("[diag] INPUT ONLY: this program sends nothing to any robot. Recording to " + record)
        secs = {n: a.phase_seconds for n, _, _ in PHASES} if a.phase_seconds else {}
        phases = run_live(record, secs, not a.no_prompt, a.hz, a.phase, a.lead_in_s)
        label = f"LIVE{' phases ' + ','.join(a.phase) if a.phase else ''}, recorded to {record}"
    results = [score_phase(n, fr, t0) for n, fr, t0 in phases]
    text, ok = format_report(results, label, len(a.phase) if a.phase and not a.replay else len(PHASES)
                             if not a.replay else len(results))
    print(text)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
