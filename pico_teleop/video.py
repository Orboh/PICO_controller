"""PICO video (FPV) on/off from the controller (log-031).

Hold the LEFT controller's Y button for 1 s: the video sender (OrinVideoSender, Orboh fork `vector-x86`,
low-latency H.264 to the XRoboToolkit app's Remote Vision panel) starts; hold again: it stops.
Lives inside the teleop sender because only one program may read the PICO (xr_input lock).
Y is free: A turns the app's sending off, B switches the video panel's 2D/3D view.
The video never moves the arm; it only uses a camera (one process per camera: not while lerobot-record
opens the same camera, use openarm-orboh start-collection-cameras.sh for that, log-031).
"""
from __future__ import annotations

import os
import shlex
import subprocess
from typing import Callable

DEFAULT_BIN = os.path.expanduser("~/projects/XRoboToolkit-Orin-Video-Sender/OrinVideoSender")


class VideoToggle:
    """Hold-to-toggle for a child process. `update(pressed, now)` returns an event text or None."""

    def __init__(self, cmd: list[str], hold_s: float = 1.0,
                 spawn: Callable[[list[str]], subprocess.Popen] | None = None, log_path: str | None = None):
        self.cmd, self.hold_s = cmd, hold_s
        self.spawn = spawn or self._spawn
        self.log_path = log_path
        self.proc: subprocess.Popen | None = None
        self.down_since: float | None = None
        self.fired = False                   # one toggle per press, however long it is held

    def _spawn(self, cmd: list[str]) -> subprocess.Popen:
        out = open(self.log_path, "ab") if self.log_path else subprocess.DEVNULL
        return subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)

    @property
    def on(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def update(self, pressed: bool, now: float) -> str | None:
        """Feed the button every cycle. Also reports a video sender that died by itself."""
        if self.proc is not None and self.proc.poll() is not None:
            code, self.proc = self.proc.returncode, None
            return f"VIDEO stopped by itself (exit {code}): check the camera device / log; hold Y 1 s to retry"
        if not pressed:
            self.down_since, self.fired = None, False
            return None
        if self.down_since is None:
            self.down_since = now
        if self.fired or now - self.down_since < self.hold_s:
            return None
        self.fired = True
        return self.stop() if self.on else self.start()

    def start(self) -> str:
        try:
            self.proc = self.spawn(self.cmd)
        except OSError as e:
            self.proc = None
            return f"VIDEO failed to start: {e}"
        return (f"VIDEO ON ({shlex.join(self.cmd)}): in the headset, Remote Vision -> ZEDMINI -> Listen -> "
                "this PC's IP, port 13579. Hold Y 1 s to stop")

    def stop(self) -> str:
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=3.0)
        self.proc = None
        return "VIDEO OFF (hold Y 1 s to start again)"
