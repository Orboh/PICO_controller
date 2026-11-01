"""Collection session behind the GUI (log-038, review fixes log-039): one arm, two methods (leader arm or PICO),
the same recorder.

Learned from the Dream-Lab-OpenArm Collect tab (`october 23/workspace/collect.py`): the recorder runs UNCHANGED
in a pseudo-terminal and the page types its keys (right arrow, left arrow, `a`, Esc) into it, so the data is saved
exactly as from the terminal; the page only shows what the scripts print. New here: the teleop side (leader agent
or PICO sender) is a second child, the PICO sender's per-cycle JSONL is read for live status, and the runbook's
rules are checked before every action (go/no-go, R2 order and one recorder per sender, R4 release before esc,
LOCKED stops the block, the leader arm held before its agent stops).

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
STAGES = ("dry", "gate", "session")       # the Collect tab's pipeline (instructions §3.3): check, 10 for review, collect
STAGE_EPISODES = {"dry": 2, "gate": 10}
SIDES = ("left", "right", "both")
ORBOH = pathlib.Path("~/projects/openarm-orboh").expanduser()
BIMANUAL_ENV = ORBOH / "config" / "bimanual-collect.kitakyushu_takeei_sort.env"   # the Collect tab's default task file
KEYS = {"next": b"\x1b[C", "rerecord": b"\x1b[D", "reset": b"a", "stop": b"\x1b"}
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[()][A-Za-z0-9]|\r")
RECORDING = re.compile(r"Recording episode (\d+)|録画中 episode (\d+)")   # an episode starts (fork's banner, 2026-10)
WAITING = re.compile(r"Waiting:|Escape key pressed")                       # an episode ended (lerobot fork log line)
RUN_DIR = re.compile(r"\[record(?:-bi)?\] 記録: (\S+)")                    # printed at the end (single arm / bimanual)
SHA = re.compile(r"sha256 ([0-9a-f]{12})")
EXPECTED_SHA = {"left": "2ec31fb82bf0", "right": "a3b97b8e9d66"}
OTHER_SENDERS = ("pico_teleop.sender", "leader_agent", "run_leader_agent", "lerobot-record", "lerobot-teleoperate")
OPERATOR_NAME = re.compile(r"[A-Za-z][A-Za-z0-9 ._-]*")   # latin letters: it goes into the dataset manifest as typed
EMERGENCY = " — in an emergency: the e-stop, not this page."
STALE_S = 1.0             # a sender status older than this is not trusted (crashed sender, Elephant GUI #2)


class SessionError(Exception):
    """An action is refused now; the message says why in plain words."""


@dataclasses.dataclass
class Options:
    """What the page's settings pass to the scripts (locked once ① is pressed)."""

    method: str = "pico"
    side: str = "left"
    stage: str = "dry"
    episodes: int = 5
    episode_time_s: int = 30
    operator: str = ""
    task: str = "Pick up the object and place it in the bin"
    smooth: bool = True        # PICO: --hz 60 --max-joint-acc-dps2 300 (log-037)
    wrist: bool = False        # PICO: --orientation
    premove_cm: float = 0.0    # PICO: Enter moves the arm forward (log-028)
    tag: str = ""              # both arms, session only: collect_bimanual.sh tag suffix (e.g. s2)
    scene: str = "none"        # one arm, gate/session: an openarm-orboh scenes/<name>.yaml (dry: none)


def bimanual_leader(o: Options) -> bool:
    """Both arms with the leader arms: the team's collect_bimanual.sh reads the leader arms itself (log-043)."""
    return o.method == "leader" and o.side == "both"


def teleop_command(o: Options, log_path: pathlib.Path, repo: pathlib.Path = REPO) -> list[str]:
    """The teleop child: the PICO sender (with its JSONL log) or the leader agent. Both arms with the leader: a
    placeholder that only waits (the recorder connects both leader arms itself, like the Collect tab)."""
    if bimanual_leader(o):
        return ["bash", "-c", "echo '[gui] both arms: the leader arms are read by collect_bimanual.sh (no leader agent)'; "
                "exec sleep infinity"]
    if o.method == "leader":
        return ["bash", str(repo / "scripts" / "leader_sender.sh"), o.side]
    # 30 s link pause (episode saves pause the receiver 1-6 s); one recorder per sender start is enforced, so a
    # restarted follower never gets this sender's held pose (R2, Elephant GUI #1)
    argv = ["bash", str(repo / "scripts" / "pico_env.sh"), "python", "-u", "-m", "pico_teleop.sender", "--host", "127.0.0.1",
            "--arms", o.side, "--confirm-follower-at-start-pose", "--max-link-pause-s", "30", "--log", str(log_path)]
    if o.smooth:
        argv += ["--hz", "60", "--max-joint-acc-dps2", "300"]
    if o.wrist:
        argv.append("--orientation")
    if o.premove_cm > 0:
        argv += ["--premove-forward-cm", f"{o.premove_cm:g}"]
    return argv


def repo_id(o: Options) -> str:
    """Dataset name per stage: dry stays a throw-away `_tmp`; gate and session name method, arm and stage
    (record_pick_place_openarm.sh stamps the date/time onto it). Local only: nothing is pushed to the Hub."""
    if o.stage == "dry":
        return f"Orboh/openarm_pilot_{o.method}_tmp"
    return f"Orboh/openarm_{o.method}_{o.side}_{o.stage}"


def recorder_command(o: Options, repo: pathlib.Path = REPO) -> tuple[list[str], dict[str, str]]:
    """The recorder child: `pilot_record.sh` (one arm, both methods) or, for both arms with the leader arms,
    openarm-orboh `collect_bimanual.sh <dry|gate|session> [tag] [n]` exactly as the Collect tab runs it."""
    if bimanual_leader(o):
        argv = ["bash", str(ORBOH / "scripts" / "collect_bimanual.sh"), o.stage]
        if o.stage == "session":
            argv.append(o.tag.strip())
        argv.append(str(int(o.episodes)))
        env = {"ENV_FILE": str(BIMANUAL_ENV), "OPERATOR": o.operator.strip(), "EPISODE_TIME_S": str(int(o.episode_time_s)),
               "PLAY_SOUNDS": "true", "BILATERAL_FEEDBACK": "true", "SAVE_ON_EPISODE_END": "true",
               "RETURN_ON_EPISODE_END": "true", "START_POSE_HOLD": "true", "START_COUNTDOWN_S": "3",
               "L_BILATERAL_GRIPPER_KP": "1.25", "L_BILATERAL_GRIPPER_KD": "0.06",   # the Collect tab's runbook values
               "DISPLAY": None, "WAYLAND_DISPLAY": None}
        return argv, env
    env = {"SIDE": o.side, "EPISODE_TIME_S": str(int(o.episode_time_s)), "TASK": o.task, "OPERATOR": o.operator.strip(),
           "REPO_ID": repo_id(o), "SCENE": "none" if o.stage == "dry" else o.scene.strip(),
           "TAG": f"{o.method}-{o.side}-{o.stage}",                 # run folder ~/collections/<UTC>-<tag> (Elephant 11-01 #6)
           # log-042: with a desktop DISPLAY lerobot reads → ← Esc from the GLOBAL keyboard (pynput), not from its
           # terminal, so the page's keys were ignored (only `a` arrived). Without a display it reads the terminal.
           "DISPLAY": None, "WAYLAND_DISPLAY": None}
    return ["bash", str(repo / "scripts" / "pilot_record.sh"), o.method, str(int(o.episodes))], env


class PtyChild:
    """A child process on a pseudo-terminal: its output lines are kept, keys can be typed into it."""

    def __init__(self, argv: list[str], env: dict[str, str] | None = None, cwd: pathlib.Path = REPO,
                 on_line: Callable[[str], None] | None = None, tail: int = 400):
        self.lines: deque[str] = deque(maxlen=tail)
        self.count = 0                       # lines ever read (the deque rolls over; Elephant GUI #8)
        self.on_line = on_line
        master, slave = pty.openpty()
        full_env = {**os.environ, **{k: v for k, v in (env or {}).items() if v is not None}}
        for k, v in (env or {}).items():
            if v is None:
                full_env.pop(k, None)          # None = remove from the child's environment
        self.proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, cwd=str(cwd), env=full_env,
                                     start_new_session=True, close_fds=True)
        os.close(slave)
        self.master = master
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    def new_lines(self, seen: int) -> tuple[list[str], int]:
        """Lines after `seen` (as many as are still kept) and the new count."""
        n = self.count - seen
        items = list(self.lines)
        return (items[-n:] if 0 < n <= len(items) else items if n > 0 else []), self.count

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
                    self.count += 1
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
    foreign: bool = False
    last_event: str = ""
    wall_s: float = 0.0
    t: float = 0.0
    read_at: float = 0.0        # time.monotonic() when the latest cycle was read


class SenderMonitor:
    """Tails the PICO sender's JSONL (one line per cycle) and keeps a live summary plus the event list."""

    def __init__(self, path: pathlib.Path, side: str, facing: Callable[[list], float | None] | None = None):
        self.path, self.side, self.facing = path, side, facing
        self.status = ArmStatus()
        self.events: deque[tuple[float, str]] = deque(maxlen=200)
        self.n_events = 0
        self.hz = 50.0
        self._pos = 0
        self._dev_ts: deque[tuple[float, int]] = deque(maxlen=200)
        self._t0: float | None = None
        self.wall_cycles = 0

    def fresh(self, now: float | None = None) -> bool:
        return (now or time.monotonic()) - self.status.read_at < STALE_S

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
        got = False
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
            got = True
        if got:
            self.status.read_at = time.monotonic()
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
            if "ANOTHER SENDER" in ev:
                s.locked = s.foreign = True
            elif "LOCKED" in ev and "refused" not in ev:
                s.locked = True
            if not ev.startswith("engage refused") or not self.events or not self.events[-1][1].startswith("engage refused"):
                self.events.append((s.t, ev))
                self.n_events += 1


def other_senders(pgrep: Callable[[str], bool] | None = None) -> list[str]:
    """Programs that must not run when a session starts (another sender would drive the same arm)."""
    def default(pattern: str) -> bool:
        return subprocess.run(["pgrep", "-f", pattern], capture_output=True, check=False).returncode == 0
    probe = pgrep or default
    return [p for p in OTHER_SENDERS if probe(p)]


GOOD_CELL = re.compile(r"^\**\s*(pass|accepted|n/a|left|right|0\.1025)", re.I)


def go_no_go_today(runbook: pathlib.Path = REPO / "docs" / "hardware-runbook.md", today: dt.date | None = None,
                   side: str | None = None) -> tuple[bool, str]:
    """Today's go/no-go column: every cell must start with PASS / accepted / n/a / the side (Elephant GUI #3),
    and item 5 must name the arm used. Returns (ok, plain reason)."""
    day = (today or dt.date.today()).isoformat()
    try:
        lines = runbook.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False, "runbook not found"
    header = next((l for l in lines if l.startswith("| # | Item")), "")
    cols = [c.strip() for c in header.strip("|").split("|")]
    if day not in cols:
        return False, f"no go/no-go column for {day} in docs/hardware-runbook.md: fill it first"
    k = cols.index(day)
    bad, item5 = [], ""
    for l in lines:
        if re.match(r"\|\s*\d+\s*\|", l):
            cells = [c.strip() for c in l.strip("|").split("|")]
            cell = cells[k] if k < len(cells) else ""
            if cells[0] == "5":
                item5 = cell
            if not cell or "pending" in cell.lower() or "fail" in cell.lower() or not GOOD_CELL.match(cell):
                bad.append(cells[0])
    if bad:
        return False, "go/no-go items not PASS: " + ", ".join(bad)
    need = ("left", "right") if side == "both" else ((side,) if side else ())
    if any(x not in item5.lower() for x in need):
        return False, f"go/no-go item 5 does not approve the {side} arm today ({item5[:60]})"
    return True, "all items PASS" + (f" · item 5: {item5[:70]}" if item5 else "")


REVIEWS = REPO / "runs" / "gui-reviews.json"


def links_down(method: str, side: str) -> list[str]:
    """CAN interfaces that are not up: the follower's, and the leader's for the leader method."""
    sides = ["l", "r"] if side == "both" else [side[0]]
    names = [f"can_fol_{x}" for x in sides] + ([f"can_lead_{x}" for x in sides] if method == "leader" else [])
    down = []
    for n in names:
        try:
            state = (pathlib.Path("/sys/class/net") / n / "operstate").read_text().strip()
        except OSError:
            state = "missing"
        if state not in ("up", "unknown"):
            down.append(f"{n} {state}")
    return down


def load_reviews(path: pathlib.Path = REVIEWS) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


class CollectionSession:
    """Teleop child + recorder child + notes, with the runbook's order and safety rules."""

    def __init__(self, runs_dir: pathlib.Path = REPO / "runs", spawn: Callable[..., PtyChild] = PtyChild,
                 probe: Callable[[], list[str]] = other_senders, facing: Callable[[list], float | None] | None = None,
                 gng: Callable[[str], tuple[bool, str]] | None = None, reviews: pathlib.Path | None = None,
                 links: Callable[[str, str], list[str]] | None = None):
        self.runs_dir, self.spawn, self.probe, self.facing = runs_dir, spawn, probe, facing
        self.gng = gng or (lambda side: go_no_go_today(side=side))
        self.links = links or links_down        # CAN links that must be up before ③ (log-041)
        self.recorder_error = ""                 # the recorder printed a Python error before any episode
        self.keys_from_page: bool | None = None  # lerobot said it reads keys from its terminal (log-042 check)
        self.saved_episodes: int | None = None   # from the run folder's manifest after the recorder exits
        self.end_pressed_at: float | None = None
        self.reviews_path = reviews or (runs_dir / "gui-reviews.json")
        self.gate_done: dict | None = load_reviews(self.reviews_path).get("pending")  # a complete gate waiting for the OK
        self.opts = Options()
        self.teleop: PtyChild | None = None
        self.recorder: PtyChild | None = None
        self.monitor: SenderMonitor | None = None
        self.tag = ""
        self.spotter = ""
        self.power_confirmed = False
        self.recorder_used = False           # one recorder per teleop start (R2, Elephant GUI #1)
        self.episode: int | None = None
        self.recording_now = False
        self.episode_started: float | None = None
        self.run_dir: str | None = None
        self.dataset: str | None = None
        self.episode_walls: dict[int, float] = {}
        self.notes: dict[int, dict] = {}
        self.alarm = ""                      # a child died while it mattered (shown as a red banner)
        self.controller: str | None = None   # the one browser client allowed to press buttons
        self._wall_at_start = 0.0
        self._lock = threading.Lock()
        self._teleop_was_alive = self._recorder_was_alive = False
        self.log: deque[str] = deque(maxlen=300)
        self.n_log = 0

    # ---- state ---------------------------------------------------------------------------------------------
    @property
    def teleop_running(self) -> bool:
        return self.teleop is not None and self.teleop.alive

    @property
    def recorder_running(self) -> bool:
        return self.recorder is not None and self.recorder.alive

    @property
    def running(self) -> bool:
        return self.teleop_running or self.recorder_running

    def arm_engaged(self) -> bool:
        """ENGAGED only from a live sender with a fresh status (a crashed sender's last line is not trusted)."""
        return (self.monitor is not None and self.teleop_running and self.monitor.fresh()
                and self.monitor.status.state == "ENGAGED")

    def locked(self) -> bool:
        return self.monitor is not None and self.monitor.status.locked

    def sha_line(self) -> tuple[str | None, bool]:
        sha = getattr(self, "_sha", None)
        return sha, sha == EXPECTED_SHA[self.opts.side]

    # ---- actions -------------------------------------------------------------------------------------------
    def start_teleop(self, o: Options) -> str:
        if self.running:
            raise SessionError("a session is already running: stop it first")
        if o.method not in METHODS or o.side not in SIDES:
            raise SessionError("choose a method and an arm")
        if not OPERATOR_NAME.fullmatch(o.operator.strip()):
            raise SessionError("type the operator's name in latin letters (it goes into the dataset)")
        if o.stage not in STAGES:
            raise SessionError("choose a method and an arm")
        if o.method == "pico" and o.side == "both":
            raise SessionError("both arms with the PICO needs a two-arm remote receiver in lerobot (not there yet): "
                               "use one arm, or the leader arms for both")
        if bimanual_leader(o) and o.stage == "session" and not re.fullmatch(r"[A-Za-z0-9-]+", o.tag.strip()):
            raise SessionError("both arms, session: type a tag (letters, digits, -), e.g. s2")
        if not bimanual_leader(o) and o.stage != "dry" and (o.scene.strip() in ("", "none")
                                                            or not (ORBOH / "scenes" / f"{o.scene.strip()}.yaml").exists()):
            raise SessionError("gate / session need a scene profile (openarm-orboh scenes/<name>.yaml), not 'none'")
        if o.stage == "session" and not self.gate_ok(o.method, o.side, o.task):
            raise SessionError(f"a session needs a reviewed gate of 10 for {o.method} / {o.side} first: record a gate, "
                               "show it to the reviewer, record their OK")
        ok, why = self.gng(o.side)
        if not ok:
            raise SessionError(why)
        busy = self.probe()
        if busy:
            raise SessionError("another program already drives an arm: " + ", ".join(busy) + " (stop it first, rule R2)")
        self.opts = dataclasses.replace(o, operator=o.operator.strip())
        if o.stage == "gate":                       # a new gate cancels the old OK (the Collect tab's rule)
            data = load_reviews(self.reviews_path)
            if data.pop(f"{o.method}_{o.side}", None) is not None:
                self.reviews_path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        self.tag = f"gui-{o.method}-{o.side}-{dt.datetime.now():%Y%m%d-%H%M%S}"
        log_path = self.runs_dir / f"{self.tag}-sender.jsonl"
        self._sha = None
        self.teleop = self.spawn(teleop_command(self.opts, log_path), on_line=self._teleop_line)
        self.monitor = SenderMonitor(log_path, o.side, self.facing) if o.method == "pico" else None
        self.power_confirmed, self.recorder_used, self.spotter, self.alarm = False, False, "", ""
        self.episode, self.recording_now, self.run_dir, self.dataset = None, False, None, None
        self.episode_walls, self.notes = {}, {}
        self._teleop_was_alive, self._recorder_was_alive = True, False
        self._note(f"① started {o.method} teleop for the {o.side} arm ({self.tag}), operator {self.opts.operator}")
        return self.tag

    def confirm_power(self, spotter: str) -> None:
        """The spotter said 'arm may move' and switched the arm's power on (the page cannot see it)."""
        if not self.teleop_running:
            raise SessionError("start the teleop first (rule R2: sender, then power, then the follower)")
        if not OPERATOR_NAME.fullmatch(spotter.strip()):
            raise SessionError("type the spotter's name: the spotter confirms, not the page operator")
        if spotter.strip().casefold() == self.opts.operator.strip().casefold():
            raise SessionError("the spotter must be a different person from the operator (runbook: a second person at the arm)")
        if self.recorder_used:
            raise SessionError("this teleop already had a recorder: ④ stop the teleop and start again (rule R2)")
        self.spotter, self.power_confirmed = spotter.strip(), True
        self._note(f"② spotter {self.spotter}: \"arm may move\", {self.opts.side} arm power ON, area clear")

    def start_recorder(self) -> None:
        if not self.teleop_running:
            raise SessionError("start the teleop first")
        if self.recorder_used:
            raise SessionError("one recording per teleop start: ④ stop the teleop, then ① again (rule R2)")
        if not self.power_confirmed:
            raise SessionError("the spotter must confirm 'arm may move' and the arm's power first (②)")
        if self.locked():
            raise SessionError("the sender is LOCKED: stop the teleop (④) and start again" + EMERGENCY)
        if self.opts.method == "pico":
            sha, ok = self.sha_line()  # one arm only (PICO both arms is refused at ①)
            if not ok:
                raise SessionError(f"start pose sha is {sha or 'not printed yet'}, expected {EXPECTED_SHA[self.opts.side]}")
        down = self.links(self.opts.method, self.opts.side)
        if down:
            raise SessionError("CAN link down: " + ", ".join(down) + " — arm power on? run canup.sh (openarm-orboh), then try ③ again")
        argv, env = recorder_command(self.opts)
        self.recorder_used, self.recorder_error, self.end_pressed_at = True, "", None
        self.keys_from_page, self.saved_episodes = None, None
        self.recorder = self.spawn(argv, env=env, on_line=self._recorder_line)
        self._recorder_was_alive = True
        self._note("③ recorder started: THE ARM MOVES BY ITSELF to the start pose")

    def key(self, name: str, hands_off: bool = False) -> None:
        """Type a recorder key, with the runbook's refusals."""
        if name not in KEYS:
            raise SessionError(f"unknown key {name}")
        if not self.recorder_running:
            raise SessionError("the recorder is not running")
        if name in ("next", "rerecord") and self.locked():
            raise SessionError("the sender is LOCKED: end the recording, stop the teleop, start again" + EMERGENCY)
        if name in ("stop", "reset", "rerecord") and self.arm_engaged():
            raise SessionError("the operator is still squeezing: release the grip first (rule R4)" + EMERGENCY)
        if name in ("stop", "reset") and self.opts.method == "leader" and not hands_off:
            raise SessionError("leader method: confirm the leader arm is held still / hands ready (R4)" + EMERGENCY)
        self.recorder.send(KEYS[name])
        if name == "stop":
            self.end_pressed_at = time.monotonic()
        if name == "rerecord" and self.episode is not None:
            with self._lock:
                self.notes.pop(self.episode, None)       # the discarded take's notes go with it
                self.episode_walls.pop(self.episode, None)
        self._note({"next": "→ next / start episode", "rerecord": "← discard this take and re-record",
                    "reset": "a: return to the start pose (the arm moves" + (", the leader arm too" if self.opts.method == "leader" else "") + ")",
                    "stop": "esc: end recording (the arm returns and goes limp)"}[name])
        self.save_notes()

    def gate_ok(self, method: str, side: str, task: str | None = None) -> dict | None:
        """A reviewed, complete gate for this method + arm (+ the same task text when given)."""
        r = load_reviews(self.reviews_path).get(f"{method}_{side}")
        if not r or not r.get("reviewer"):
            return None
        return r if task is None or r.get("task") in (None, task) else None

    def record_review(self, reviewer: str) -> None:
        """The reviewer looked at the finished gate and gave the OK (the Collect tab's V4 rule)."""
        if not OPERATOR_NAME.fullmatch(reviewer.strip()):
            raise SessionError("type the reviewer's name")
        if not self.gate_done:
            raise SessionError("no finished gate to review")
        data = load_reviews(self.reviews_path)
        data.pop("pending", None)
        data[f"{self.gate_done['method']}_{self.gate_done['side']}"] = {
            **self.gate_done, "reviewer": reviewer.strip(), "ok_at": dt.datetime.now().isoformat(timespec="seconds")}
        self.reviews_path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        self._note(f"gate OK recorded: reviewer {reviewer.strip()} ({self.gate_done['episodes']} episodes)")
        self.gate_done = None

    def premove(self) -> None:
        if not (self.teleop_running and self.opts.method == "pico" and self.opts.premove_cm > 0):
            raise SessionError("pre-move needs a running PICO sender started with a pre-move distance")
        if not (self.power_confirmed and self.recorder_running):
            raise SessionError("pre-move only after ② and ③ (the arm is at the start pose)")
        if self.recording_now:
            raise SessionError("not during an episode")
        if not (self.monitor and self.monitor.fresh() and self.monitor.status.state == "HOLD"):
            raise SessionError("the sender must be in HOLD (grip released) with a fresh status")
        self.teleop.send(b"\n")
        self._note(f"pre-move: the arm moves {self.opts.premove_cm:g} cm forward slowly")

    def recorder_stuck(self) -> bool:
        """The recorder failed before any episode, or did not end 60 s after End recording (saving the last episode
        took up to 22 s in real runs; Elephant 11-01 #2)."""
        return self.recorder_running and (bool(self.recorder_error) or
                                          (self.end_pressed_at is not None and time.monotonic() - self.end_pressed_at > 60.0))

    def abort_recorder(self, confirmed: bool = False) -> None:
        """Ctrl-C to the recorder (only when it is stuck). A powered follower goes limp: the spotter supports it."""
        if not self.recorder_running:
            raise SessionError("the recorder is not running")
        if not self.recorder_stuck():
            raise SessionError("use End recording first; abort only when the recorder does not end")
        if not confirmed:
            raise SessionError("confirm: the follower arm is off or supported (Ctrl-C makes a powered follower go limp)")
        self.recorder.interrupt()
        self._note("recorder aborted (Ctrl-C) after it did not end" + (f": {self.recorder_error}" if self.recorder_error else ""))

    def stop_teleop(self, leader_held: bool = False) -> None:
        """Ctrl-C to the teleop child. Order: recorder first (rule R2/R4). Leader: the leader arm must be held."""
        if not self.teleop_running:
            raise SessionError("the teleop is not running")
        if self.recorder_running:
            raise SessionError("end the recording first (End recording), then stop the teleop")
        if self.opts.method == "leader" and not leader_held:
            raise SessionError("Ctrl-C makes the LEADER arm go limp: hold the leader arm, then confirm")
        self.teleop.interrupt()
        self._note("④ teleop stopped (Ctrl-C)")

    def shutdown(self) -> list[str]:
        """The GUI is closing (Elephant GUI #7): end the recorder with Esc, stop a PICO sender with Ctrl-C.
        A leader agent is NOT stopped (its arm would go limp in the operator's hands): it is reported."""
        msgs = []
        if self.recorder_running:
            try:
                self.recorder.send(KEYS["stop"])
            except (SessionError, OSError):
                pass
            if self.recorder.wait(20.0) is None:
                self.recorder.interrupt()
            msgs.append("recorder ended (Esc)")
        if self.teleop_running:
            if self.opts.method == "pico":
                self.teleop.interrupt()
                msgs.append("PICO sender stopped")
            else:
                msgs.append("LEADER AGENT LEFT RUNNING: hold the leader arm, then stop it in a terminal (Ctrl-C)")
        return msgs

    def set_note(self, episode: int, grasp: bool | None = None, placed: bool | None = None, text: str | None = None) -> None:
        with self._lock:
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
        with self._lock:
            eps = {str(k): {**v, "wall_s": round(self.episode_walls.get(k, 0.0), 1)} for k, v in sorted(self.notes.items())}
        p = self.runs_dir / f"{self.tag}-notes.json"
        p.write_text(json.dumps({"tag": self.tag, "options": dataclasses.asdict(self.opts), "spotter": self.spotter,
                                 "run_dir": self.run_dir, "dataset": self.dataset, "alarm": self.alarm,
                                 "episodes": eps}, indent=1), encoding="utf-8")
        return p

    # ---- updates -------------------------------------------------------------------------------------------
    def poll(self) -> None:
        if self.monitor is not None:
            self.monitor.poll()
            if self.recording_now and self.episode is not None:
                self.episode_walls[self.episode] = self.monitor.status.wall_s - self._wall_at_start
        # a child died while it mattered: alarm, flag the episode, keep the notes (goldfish GUI #3)
        if self._teleop_was_alive and not self.teleop_running:
            self._teleop_was_alive = False
            code = self.teleop.proc.returncode if self.teleop else None
            if self.recorder_running:
                self.alarm = (f"TELEOP EXITED (code {code}) WHILE RECORDING: spotter watch the arm; release, then End recording"
                              + EMERGENCY)
                self._flag("teleop exited")
            self._note(f"teleop exited (code {code})")
        if self._recorder_was_alive and not self.recorder_running:
            self._recorder_was_alive = False
            code = self.recorder.proc.returncode if self.recorder else None
            if self.recording_now:
                self.alarm = f"RECORDER EXITED (code {code}) DURING AN EPISODE: check the dataset" + EMERGENCY
                self._flag("recorder exited")
            self.recording_now, self.power_confirmed = False, False
            self._read_manifest()
            if (self.opts.stage == "gate" and code == 0 and self.saved_episodes is not None
                    and self.saved_episodes >= self.opts.episodes):     # only a complete gate can be reviewed (Elephant #1)
                self.gate_done = {"method": self.opts.method, "side": self.opts.side, "tag": self.tag, "dataset": self.dataset,
                                  "episodes": self.saved_episodes, "task": self.opts.task}
                data = load_reviews(self.reviews_path)
                data["pending"] = self.gate_done
                self.reviews_path.write_text(json.dumps(data, indent=1), encoding="utf-8")
            elif self.opts.stage == "gate":
                self._note(f"gate not complete (exit {code}, saved {self.saved_episodes} of {self.opts.episodes}): no review")
            saved = (f"; SAVED {self.saved_episodes} of {self.opts.episodes} episodes" if self.saved_episodes is not None
                     else "; saved episodes unknown (no manifest)")
            self._note(f"recorder exited (code {code}){saved}; next: ④ stop the teleop" + (f"; data: {self.dataset}" if self.dataset else ""))
            if not self.alarm and (self.saved_episodes or 0) == 0:
                self.alarm = "NO EPISODE WAS SAVED in this recording: check the recorder output before going on"
            self.save_notes()

    def _flag(self, why: str) -> None:
        if self.episode is not None:
            with self._lock:
                n = self.notes.setdefault(self.episode, {"grasp": False, "placed": False, "text": ""})
                n["text"] = (n["text"] + f" [{why}]").strip()

    def _read_manifest(self) -> None:
        if not self.run_dir:
            return
        try:
            m = json.loads((pathlib.Path(self.run_dir) / "manifest.json").read_text(encoding="utf-8"))
            art = m.get("artifacts") or {}
            self.dataset = art.get("dataset_root") or self.dataset
            if art.get("episodes_recorded") is not None:
                self.saved_episodes = int(art["episodes_recorded"])
        except (OSError, ValueError):
            pass

    def _teleop_line(self, text: str) -> None:
        m = SHA.search(text)
        if m and getattr(self, "_sha", None) is None:
            self._sha = m.group(1)          # kept for the whole run (Elephant GUI #9)

    def _recorder_line(self, text: str) -> None:
        m = RECORDING.search(text)
        if m:
            with self._lock:
                self.episode = int(m.group(1) or m.group(2))
                self.recording_now, self.episode_started = True, time.monotonic()
                self.notes.setdefault(self.episode, {"grasp": False, "placed": False, "text": ""})
                self._wall_at_start = self.monitor.status.wall_s if self.monitor else 0.0
        elif WAITING.search(text):
            self.recording_now = False
        if "No display detected: reading recording control keys from stdin" in text:
            self.keys_from_page = True
        elif "pynput" in text.lower() and self.keys_from_page is None:
            self.keys_from_page = False
        if self.episode is None and re.match(r"\w*(Error|Exception)\b.*:", text):
            self.recorder_error = text[:160]          # e.g. "ConnectionError: Failed to connect to CAN bus: …"
            self.alarm = f"RECORDER FAILED: {self.recorder_error} — press Abort recorder, then ④"
        r = RUN_DIR.search(text)
        if r:
            self.run_dir = r.group(1)

    def _note(self, text: str) -> None:
        self.log.append(f"{dt.datetime.now():%H:%M:%S}  {text}")
        self.n_log += 1
