"""PICO video toggle (log-031): hold left Y 1 s = start / stop; one toggle per press; dead process reported."""
from pico_teleop.video import VideoToggle


class FakeProc:
    def __init__(self):
        self.returncode, self.alive = None, True

    def poll(self):
        return None if self.alive else self.returncode

    def terminate(self):
        self.alive, self.returncode = False, -15

    def wait(self, timeout=None):
        return self.returncode

    kill = terminate


def _toggle():
    started = []
    v = VideoToggle(["OrinVideoSender"], hold_s=1.0, spawn=lambda cmd: started.append(FakeProc()) or started[-1])
    return v, started


def _hold(v, t0, secs, dt=0.02):
    evs, t = [], t0
    while t < t0 + secs:
        evs.append(v.update(True, t)); t += dt
    evs.append(v.update(False, t))
    return [e for e in evs if e], t


def test_short_press_does_nothing():
    v, started = _toggle()
    evs, _ = _hold(v, 0.0, 0.5)
    assert evs == [] and not started and not v.on


def test_hold_starts_then_hold_again_stops():
    v, started = _toggle()
    evs, t = _hold(v, 0.0, 1.5)
    assert len(evs) == 1 and evs[0].startswith("VIDEO ON") and v.on
    evs, t = _hold(v, t + 1.0, 3.0)                     # a long hold still toggles only once
    assert len(evs) == 1 and evs[0].startswith("VIDEO OFF") and not v.on
    assert len(started) == 1


def test_dead_video_sender_is_reported():
    v, started = _toggle()
    _hold(v, 0.0, 1.5)
    started[0].alive, started[0].returncode = False, 1
    ev = v.update(False, 5.0)
    assert ev and "stopped by itself" in ev and not v.on
