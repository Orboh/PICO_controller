"""PICO input layer: one SDK reader for both controllers, plus fake and replay sources.

Facts from the SDK binding (XRoboToolkit-PC-Service-Pybind 75cb113, bindings/py_bindings.cpp):
- The SDK keeps the LAST received values in globals. If the headset stops streaming, the getters
  keep returning the old pose, so staleness must be detected from `get_time_stamp_ns()` not advancing.
- Before any data arrives everything is 0 (zero-initialised statics, py_bindings.cpp:56).
- Each side's globals are overwritten only when the packet contains that side
  (py_bindings.cpp:115/130), while the shared timestamp is updated separately (:152). So ONE
  controller can freeze while the timestamp keeps advancing: `FreshnessTracker` flags that as
  "frozen" (pose bit-identical across N device updates). Whether the app omits a lost controller,
  repeats its last pose or sends zeros is NOT yet observed live (gate 1 must record it).
- Each getter takes its own mutex, so one poll can mix two packets; `SdkSource.read` re-reads
  when the timestamp changed during the poll. A json error mid-packet can update the left side only.
- Poses are [x, y, z, qx, qy, qz, qw] in the OpenXR world frame (+x right, +y up, +z back).
"""
from __future__ import annotations

import fcntl
import json
import math
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Iterator, Protocol

SIDES = ("left", "right")

DEFAULT_SDK_PY = os.path.expanduser("~/Desktop/XRoboToolkit-PC-Service-Pybind/build/lib.linux-x86_64-cpython-310")
DEFAULT_SDK_LIB = "/opt/apps/roboticsservice/SDK/x64"
# Fixed path (not XDG_RUNTIME_DIR, which differs under sudo/ssh). Only readers that use this lock are
# excluded: the older draft openarm-orboh-wt-pico/pico/pico_teleop.py does not take it.
LOCK_PATH = "/tmp/pico-controller-xr-reader.lock"


@dataclass(frozen=True)
class ControllerSample:
    """One controller at one instant (raw SDK values, OpenXR frame)."""

    pose: tuple[float, ...]  # x, y, z, qx, qy, qz, qw
    grip: float
    trigger: float
    menu: bool = False
    primary: bool = False    # A (right) / X (left)
    secondary: bool = False  # B (right) / Y (left)


@dataclass(frozen=True)
class XRFrame:
    """Both controllers + headset, stamped with host monotonic time and the device timestamp."""

    t_host: float
    t_device_ns: int
    left: ControllerSample
    right: ControllerSample
    headset: tuple[float, ...] = (0.0,) * 7

    def controller(self, side: str) -> ControllerSample:
        return self.left if side == "left" else self.right

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @staticmethod
    def from_json(line: str) -> "XRFrame":
        d = json.loads(line)
        return XRFrame(
            t_host=float(d["t_host"]), t_device_ns=int(d["t_device_ns"]),
            left=ControllerSample(**{**d["left"], "pose": tuple(d["left"]["pose"])}),
            right=ControllerSample(**{**d["right"], "pose": tuple(d["right"]["pose"])}),
            headset=tuple(d.get("headset", (0.0,) * 7)),
        )


def pose_status(pose) -> str:
    """'ok' / 'nonfinite' / 'zero' (no data or tracking lost) / 'origin' / 'bad_quat' / 'bad_shape'.

    One rule shared by the diagnostic and the sender. 'origin' = position exactly (0,0,0): a short
    pose string is zero-padded by the binding, and a real controller is never exactly at the origin.
    """
    if len(pose) != 7:
        return "bad_shape"
    if not all(math.isfinite(float(v)) for v in pose):
        return "nonfinite"
    if all(float(v) == 0.0 for v in pose):
        return "zero"
    if all(float(v) == 0.0 for v in pose[:3]):
        return "origin"
    qn = math.sqrt(sum(float(v) ** 2 for v in pose[3:]))
    if abs(qn - 1.0) > 0.1:  # SDK quaternions are unit; far from 1 means a garbage sample
        return "bad_quat"
    return "ok"


def analog_ok(value: float) -> bool:
    """grip / trigger must be finite and within [0, 1] (small tolerance for float noise)."""
    return math.isfinite(value) and -0.01 <= value <= 1.01


class FreshnessTracker:
    """Shared freshness rule for diag and sender. Call `update(frame)` once per poll.

    Stream level: the device timestamp must advance (backwards = 'ts_backwards', a reconnect) and
    must not be stuck longer than `stale_s`. Side level: a valid pose that stays bit-identical for
    `frozen_updates` device updates is 'frozen' (that side is no longer being refreshed).
    """

    def __init__(self, stale_s: float = 0.1, frozen_updates: int = 10):
        self.stale_s, self.frozen_updates = stale_s, frozen_updates
        self.last_ts: int | None = None
        self.last_change_t: float | None = None
        self.same: dict[str, int] = {s: 0 for s in SIDES}
        self.last_pose: dict[str, tuple] = {}
        self.stream = "no_data"
        self.side_state: dict[str, str] = {s: "no_data" for s in SIDES}

    def update(self, fr: XRFrame) -> None:
        new = False
        if fr.t_device_ns == 0:
            self.stream = "no_data"
        elif self.last_ts is not None and fr.t_device_ns < self.last_ts:
            self.stream = "ts_backwards"
            self.last_ts, self.last_change_t = fr.t_device_ns, fr.t_host
            self.same = {s: 0 for s in SIDES}
            new = True
        elif fr.t_device_ns != self.last_ts:
            new = True
            self.last_ts, self.last_change_t = fr.t_device_ns, fr.t_host
            self.stream = "ok"
        elif self.last_change_t is not None and fr.t_host - self.last_change_t > self.stale_s:
            self.stream = "stale"
        for s in SIDES:
            pose = fr.controller(s).pose
            ps = pose_status(pose)
            if new:
                self.same[s] = self.same[s] + 1 if pose == self.last_pose.get(s) else 0
                self.last_pose[s] = pose
            if ps != "ok":
                self.side_state[s] = ps
            elif self.same[s] >= self.frozen_updates:
                self.side_state[s] = "frozen"
            else:
                self.side_state[s] = "ok"

    def fresh(self, side: str) -> bool:
        return self.stream == "ok" and self.side_state[side] == "ok"


class XRSource(Protocol):
    def read(self) -> XRFrame: ...
    def close(self) -> None: ...


class SingleReaderLock:
    """Process-wide lock so only one process reads PICO input at a time (goal.md, step 4)."""

    def __init__(self, path: str = LOCK_PATH):
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise RuntimeError(
                f"another process already reads PICO input (lock {self.path}); stop it first"
            ) from None
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


class SdkSource:
    """The single real reader. Needs the PC service running and the SDK import path set."""

    def __init__(self, lock: SingleReaderLock | None = None):
        self._lock = lock or SingleReaderLock()
        self._lock.acquire()
        try:
            import xrobotoolkit_sdk as xrt
        except ImportError as e:
            self._lock.release()
            raise RuntimeError(
                f"cannot import xrobotoolkit_sdk ({e}); run through scripts/pico_env.sh "
                f"(PYTHONPATH={DEFAULT_SDK_PY}, LD_LIBRARY_PATH={DEFAULT_SDK_LIB})"
            ) from e
        self.xrt = xrt
        try:
            xrt.init()
        except Exception:
            self._lock.release()
            raise

    def _ctrl(self, side: str) -> ControllerSample:
        x = self.xrt
        if side == "left":
            return ControllerSample(tuple(x.get_left_controller_pose()), float(x.get_left_grip()),
                                    float(x.get_left_trigger()), bool(x.get_left_menu_button()),
                                    bool(x.get_X_button()), bool(x.get_Y_button()))
        return ControllerSample(tuple(x.get_right_controller_pose()), float(x.get_right_grip()),
                                float(x.get_right_trigger()), bool(x.get_right_menu_button()),
                                bool(x.get_A_button()), bool(x.get_B_button()))

    def read(self) -> XRFrame:
        """Poll both controllers; retry (max 3) if a new packet arrived in the middle of the poll."""
        for _ in range(3):
            ts0 = int(self.xrt.get_time_stamp_ns())
            fr = XRFrame(time.monotonic(), ts0, self._ctrl("left"), self._ctrl("right"),
                         tuple(self.xrt.get_headset_pose()))
            if int(self.xrt.get_time_stamp_ns()) == ts0:
                return fr
        return fr

    def close(self) -> None:
        try:
            self.xrt.close()
        finally:
            self._lock.release()


@dataclass
class FakeSource:
    """Synthetic input for offline tests: script = list of (t_from, side -> ControllerSample).

    `clock` is injectable so tests run without sleeping. The device timestamp advances with time
    unless `freeze_after` is set (simulates the SDK keeping stale values).
    """

    script: list[tuple[float, dict[str, ControllerSample]]]
    clock: callable = time.monotonic
    freeze_after: float | None = None
    _t0: float | None = field(default=None, init=False)

    def read(self) -> XRFrame:
        now = self.clock()
        if self._t0 is None:
            self._t0 = now
        t = now - self._t0
        cur: dict[str, ControllerSample] = {}
        for t_from, sides in self.script:
            if t >= t_from:
                cur.update(sides)
        t_dev = t if self.freeze_after is None else min(t, self.freeze_after)
        idle = ControllerSample((0.0,) * 7, 0.0, 0.0)
        return XRFrame(now, int(t_dev * 1e9) + 1, cur.get("left", idle), cur.get("right", idle))

    def close(self) -> None:
        pass


class ReplaySource:
    """Replays frames recorded by `diag_input --record` (JSON lines), ignoring wall time."""

    def __init__(self, path: str):
        with open(path) as f:
            self.frames = [XRFrame.from_json(line) for line in f if line.strip()]
        if not self.frames:
            raise ValueError(f"no frames in {path}")
        self._it: Iterator[XRFrame] = iter(self.frames)
        self.exhausted = False

    def read(self) -> XRFrame:
        try:
            return next(self._it)
        except StopIteration:
            self.exhausted = True
            return self.frames[-1]

    def close(self) -> None:
        pass
