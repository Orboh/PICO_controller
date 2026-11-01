"""Collection session behind the GUI (log-038): one arm, two methods (leader arm or PICO), the same recorder.

Learned from the Dream-Lab-OpenArm Collect tab (`october 23/workspace/collect.py`): the recorder runs UNCHANGED
in a pseudo-terminal and the page types its keys (right arrow, left arrow, `a`, Esc) into it, so the data is saved
exactly as from the terminal; the page only shows what the scripts print. New here: the teleop side (leader agent
or PICO sender) is a second child, the PICO sender's per-cycle JSONL is read for live status, and the safety rules
of the runbook are checked before every action (R2 order, R4 release before esc, leader held before its stop).

No robot power is ever switched here; the spotter keeps the e-stop and the power switch.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import pathlib
import pty
import re
import signal
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable

REPO = pathlib.Path(__file__).resolve().parents[2]
METHODS = ("pico", "leader")
SIDES = ("left", "right")
KEYS = {"next": b"\x1b[C", "rerecord": b"\x1b[D", "reset": b"a", "stop": b"\x1b"}
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[()][A-Za-z0-9]|\r")
RECORDING = re.compile(r"Recording episode (\d+)|録画中 episode (\d+)")
RESET = re.compile(r"Reset the environment|リセット")
DATASET = re.compile(r"(/\S*/huggingface/lerobot/\S+)|root[=:]\s*(\S+)")
EXPECTED_SHA = {"left": "2ec31fb82bf0", "right": "a3b97b8e9d66"}
OTHER_SENDERS = ("pico_teleop.sender", "leader_agent", "run_leader_agent", "lerobot-record", "lerobot-teleoperate")


class SessionError(Exception):
    """An action is refused now; the message says why in plain words."""


@dataclasses.dataclass
class Options:
    """What the page's settings pass to the scripts."""

    method: str = "pico"
    side: str = "left"
    episodes: int = 5
    episode_time_s: int = 30
    operator: str = ""
    task: str = "Pick up the object and place it in the bin"
    smooth: bool = True        # PICO: --hz 60 --max-joint-acc-dps2 300 (log-037)
    wrist: bool = False        # PICO: --orientation
    premove_cm: float = 0.0    # PICO: Enter moves the arm forward (log-028)


def teleop_command(o: Options, log_path: pathlib.Path, repo: pathlib.Path = REPO) -> list[str]:
    """The teleop child: the PICO sender (with its JSONL log) or the leader agent."""
    if o.method == "leader":
        return ["bash", str(repo / "scripts" / "leader_sender.sh"), o.side]
    argv = ["bash", str(repo / "scripts" / "pico_env.sh"), "python", "-u", "-m", "pico_teleop.sender", "--host", "127.0.0.1",
            "--arms", o.side, "--confirm-follower-at-start-pose", "--max-link-pause-s", "300", "--log", str(log_path)]
    if o.smooth:
        argv += ["--hz", "60", "--max-joint-acc-dps2", "300"]
    if o.wrist:
        argv.append("--orientation")
    if o.premove_cm > 0:
        argv += ["--premove-forward-cm", f"{o.premove_cm:g}"]
    return argv


def recorder_command(o: Options, repo: pathlib.Path = REPO) -> tuple[list[str], dict[str, str]]:
    """The recorder child: `pilot_record.sh` unchanged (same dataset settings for both methods)."""
    env = {"SIDE": o.side, "EPISODE_TIME_S": str(int(o.episode_time_s)), "TASK": o.task}
    if o.operator.strip():
        env["OPERATOR"] = o.operator.strip()
    return ["bash", str(repo / "scripts" / "pilot_record.sh"), o.method, str(int(o.episodes))], env


class PtyChild:
    """A child process on a pseudo-terminal: its output lines are kept, keys can be typed into it."""

    def __init__(self, argv: list[str], env: dict[str, str] | None = None, cwd: pathlib.Path = REPO,
                 on_line: Callable[[str], None] | None = None, tail: int = 400):
        self.lines: deque[str] = deque(maxlen=tail)
        self.on_line = on_line
        master, slave = pty.openpty()
        full_env = {**os.environ, **(env or {})}
        self.proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, cwd=str(cwd), env=full_env,
                                     start_new_session=True, close_fds=True)
        os.close(slave)
        self.master = master
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    def _read(self) -> None:
        buf = b""
        while True:
            try:
                data = os.read(self.master, 4096)
            except OSError:
                break
            if not data:
                break
            buf += data
            *parts, buf = re.split(rb"[\n\r]", buf)
            for raw in parts:
                text = ANSI.sub("", raw.decode("utf-8", "replace")).strip()
                if text:
                    self.lines.append(text)
                    if self.on_line:
                        self.on_line(text)
        try:
            os.close(self.master)
        except OSError:
            pass

    def send(self, data: bytes) -> None:
        if not self.alive:
            raise SessionError("that program is not running")
        os.write(self.master, data)

    def interrupt(self) -> None:
        """Ctrl-C (SIGINT) to the whole process group, like pressing Ctrl-C in its terminal."""
        if self.alive:
            os.killpg(self.proc.pid, signal.SIGINT)

    def wait(self, timeout: float = 5.0) -> int | None:
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return None


@dataclasses.dataclass
class ArmStatus:
    """The latest PICO sender cycle, read from its JSONL log."""

    state: str = "—"
    link: str = "—"
    wall: str | None = None
    input_hz: float = 0.0
    facing_deg: float | None = None
    locked: bool = False
    last_event: str = ""
    wall_s: float = 0.0
    t: float = 0.0


class SenderMonitor:
    """Tails the PICO sender's JSONL (one line per cycle) and keeps a live summary plus the event list."""

    def __init__(self, path: pathlib.Path, side: str, facing: Callable[[list], float | None] | None = None):
        self.path, self.side, self.facing = path, side, facing
        self.status = ArmStatus()
        self.events: deque[tuple[float, str]] = deque(maxlen=200)
        self.hz = 50.0
        self._pos = 0
        self._dev_ts: deque[tuple[float, int]] = deque(maxlen=200)
        self._t0: float | None = None
        self.wall_cycles = 0

    def poll(self) -> ArmStatus:
        """Read the new lines; cheap enough to call twice per second."""
        if not self.path.exists():
            return self.status
        with open(self.path, "rb") as f:
            f.seek(self._pos)
            chunk = f.read()
        end = chunk.rfind(b"\n")
        if end < 0:
            return self.status
        self._pos += end + 1
        for line in chunk[: end + 1].splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if "t" not in d:
                if "args" in d:
                    self.hz = float(d["args"].get("hz", 50.0))
                continue
            self._cycle(d)
        return self.status

    def _cycle(self, d: dict) -> None:
        t = float(d["t"])
        self._t0 = self._t0 if self._t0 is not None else t
        a = (d.get("arms") or {}).get(self.side) or {}
        s = self.status
        s.t, s.state, s.link, s.wall = t - self._t0, a.get("state", "—"), a.get("link") or "—", a.get("wall")
        if s.wall:
            self.wall_cycles += 1
        s.wall_s = self.wall_cycles / self.hz
        fr = d.get("frame") or {}
        ts = int(fr.get("t_device_ns") or 0)
        if ts and (not self._dev_ts or self._dev_ts[-1][1] != ts):
            self._dev_ts.append((t, ts))
        while self._dev_ts and t - self._dev_ts[0][0] > 1.0:
            self._dev_ts.popleft()
        s.input_hz = float(len(self._dev_ts))
        if self.facing and fr.get("headset"):
            s.facing_deg = self.facing(fr["headset"])
        ev = a.get("event")
        if ev:
            s.last_event = ev
            if "LOCKED" in ev and "refused" not in ev:
                s.locked = True
            if not ev.startswith("engage refused") or not self.events or not self.events[-1][1].startswith("engage refused"):
                self.events.append((s.t, ev))


def other_senders(pgrep: Callable[[str], bool] | None = None) -> list[str]:
    """Programs that must not run when a session starts (another sender would drive the same arm)."""
    def default(pattern: str) -> bool:
        return subprocess.run(["pgrep", "-f", pattern], capture_output=True, check=False).returncode == 0
    probe = pgrep or default
    return [p for p in OTHER_SENDERS if probe(p)]


def go_no_go_today(runbook: pathlib.Path = REPO / "docs" / "hardware-runbook.md", today: dt.date | None = None) -> tuple[bool, str]:
    """Is today's go/no-go column in the runbook filled (all PASS / left / n/a / accepted, none pending)?"""
    day = (today or dt.date.today()).isoformat()
    try:
        lines = runbook.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False, "runbook not found"
    header = next((l for l in lines if l.startswith("| # | Item")), "")
    cols = [c.strip() for c in header.strip("|").split("|")]
    if day not in cols:
        return False, f"no go/no-go column for {day} in docs/hardware-runbook.md"
    k = cols.index(day)
    pending = []
    for l in lines:
        if re.match(r"\|\s*\d+\s*\|", l):
            cells = [c.strip() for c in l.strip("|").split("|")]
            if k < len(cells) and ("pending" in cells[k].lower() or not cells[k]):
                pending.append(cells[0])
    return (not pending, "all items filled" if not pending else "pending items: " + ", ".join(pending))


class CollectionSession:
    """Teleop child + recorder child + notes, with the runbook's order and safety rules."""

    def __init__(self, runs_dir: pathlib.Path = REPO / "runs", spawn: Callable[..., PtyChild] = PtyChild,
                 probe: Callable[[], list[str]] = other_senders, facing: Callable[[list], float | None] | None = None):
        self.runs_dir, self.spawn, self.probe, self.facing = runs_dir, spawn, probe, facing
        self.opts = Options()
        self.teleop: PtyChild | None = None
        self.recorder: PtyChild | None = None
        self.monitor: SenderMonitor | None = None
        self.tag = ""
        self.power_confirmed = False
        self.episode: int | None = None
        self.dataset: str | None = None
        self.episode_walls: dict[int, float] = {}
        self.notes: dict[int, dict] = {}
        self._wall_at_start = 0.0
        self.log: deque[str] = deque(maxlen=300)

    # ---- state ---------------------------------------------------------------------------------------------
    @property
    def teleop_running(self) -> bool:
        return self.teleop is not None and self.teleop.alive

    @property
    def recorder_running(self) -> bool:
        return self.recorder is not None and self.recorder.alive

    def arm_engaged(self) -> bool:
        return self.monitor is not None and self.monitor.status.state == "ENGAGED"

    def sha_line(self) -> tuple[str | None, bool]:
        """The sender's start-pose sha and whether it is the expected one for this arm (PICO only)."""
        if self.teleop is None:
            return None, False
        for line in self.teleop.lines:
            m = re.search(r"sha256 ([0-9a-f]{12})", line)
            if m:
                return m.group(1), m.group(1) == EXPECTED_SHA[self.opts.side]
        return None, False

    # ---- actions -------------------------------------------------------------------------------------------
    def start_teleop(self, o: Options) -> str:
        if self.teleop_running or self.recorder_running:
            raise SessionError("a session is already running: stop it first")
        if o.method not in METHODS or o.side not in SIDES:
            raise SessionError("choose a method and an arm")
        busy = self.probe()
        if busy:
            raise SessionError("another program already drives an arm: " + ", ".join(busy) + " (stop it first, rule R2)")
        self.opts = o
        self.tag = f"gui-{o.method}-{o.side}-{dt.datetime.now():%Y%m%d-%H%M%S}"
        log_path = self.runs_dir / f"{self.tag}-sender.jsonl"
        self.teleop = self.spawn(teleop_command(o, log_path), on_line=None)
        self.monitor = SenderMonitor(log_path, o.side, self.facing) if o.method == "pico" else None
        self.power_confirmed, self.episode, self.dataset = False, None, None
        self.episode_walls, self.notes = {}, {}
        self._note(f"started {o.method} teleop for the {o.side} arm ({self.tag})")
        return self.tag

    def confirm_power(self) -> None:
        """The spotter said 'arm may move' and switched the arm's power on (the page cannot see it)."""
        if not self.teleop_running:
            raise SessionError("start the teleop first (rule R2: sender, then power, then the follower)")
        self.power_confirmed = True
        self._note("spotter: arm power on, area clear")

    def start_recorder(self) -> None:
        if not self.teleop_running:
            raise SessionError("start the teleop first")
        if not self.power_confirmed:
            raise SessionError("the spotter must confirm 'arm may move' and the arm's power first")
        if self.recorder_running:
            raise SessionError("the recorder is already running")
        if self.opts.method == "pico":
            sha, ok = self.sha_line()
            if not ok:
                raise SessionError(f"start pose sha is {sha or 'not printed yet'}, expected {EXPECTED_SHA[self.opts.side]}")
        argv, env = recorder_command(self.opts)
        self.recorder = self.spawn(argv, env=env, on_line=self._recorder_line)
        self._note("recorder started: THE ARM MOVES BY ITSELF to the start pose")

    def key(self, name: str) -> None:
        """Type a recorder key. Esc (stop) is refused while a PICO arm is ENGAGED (rule R4)."""
        if name not in KEYS:
            raise SessionError(f"unknown key {name}")
        if not self.recorder_running:
            raise SessionError("the recorder is not running")
        if name == "stop" and self.arm_engaged():
            raise SessionError("the operator is still squeezing: release the grip first (rule R4), then stop")
        self.recorder.send(KEYS[name])
        self._note({"next": "→ next / start episode", "rerecord": "← re-record", "reset": "a: reset (the arm moves)",
                    "stop": "esc: end recording (the arm returns and goes limp)"}[name])

    def premove(self) -> None:
        if not (self.teleop_running and self.opts.method == "pico" and self.opts.premove_cm > 0):
            raise SessionError("pre-move needs a running PICO sender started with a pre-move distance")
        if self.arm_engaged():
            raise SessionError("release the grip first")
        self.teleop.send(b"\n")
        self._note(f"pre-move: the arm moves {self.opts.premove_cm:g} cm forward slowly")

    def stop_teleop(self, leader_held: bool = False) -> None:
        """Ctrl-C to the teleop child. Order: recorder first (rule R2/R4). Leader: the leader arm must be held."""
        if not self.teleop_running:
            raise SessionError("the teleop is not running")
        if self.recorder_running:
            raise SessionError("end the recording first (Stop recording), then stop the teleop")
        if self.opts.method == "leader" and not leader_held:
            raise SessionError("Ctrl-C makes the LEADER arm go limp: hold the leader arm, then confirm")
        self.teleop.interrupt()
        self._note("teleop stopped (Ctrl-C)")

    def set_note(self, episode: int, grasp: bool | None = None, placed: bool | None = None, text: str | None = None) -> None:
        n = self.notes.setdefault(episode, {"grasp": False, "placed": False, "text": ""})
        if grasp is not None:
            n["grasp"] = grasp
        if placed is not None:
            n["placed"] = placed
        if text is not None:
            n["text"] = text
        self.save_notes()

    def save_notes(self) -> pathlib.Path | None:
        if not self.tag:
            return None
        p = self.runs_dir / f"{self.tag}-notes.json"
        p.write_text(json.dumps({"tag": self.tag, "options": dataclasses.asdict(self.opts), "dataset": self.dataset,
                                 "episodes": {str(k): {**v, "wall_s": round(self.episode_walls.get(k, 0.0), 1)}
                                              for k, v in sorted(self.notes.items())}}, indent=1), encoding="utf-8")
        return p

    # ---- updates -------------------------------------------------------------------------------------------
    def poll(self) -> None:
        if self.monitor is not None:
            self.monitor.poll()
            if self.episode is not None:
                self.episode_walls[self.episode] = self.monitor.status.wall_s - self._wall_at_start

    def _recorder_line(self, text: str) -> None:
        m = RECORDING.search(text)
        if m:
            self.episode = int(m.group(1) or m.group(2))
            self.notes.setdefault(self.episode, {"grasp": False, "placed": False, "text": ""})
            self._wall_at_start = self.monitor.status.wall_s if self.monitor else 0.0
        d = DATASET.search(text)
        if d and not self.dataset:
            self.dataset = d.group(1) or d.group(2)

    def _note(self, text: str) -> None:
        self.log.append(f"{dt.datetime.now():%H:%M:%S}  {text}")
