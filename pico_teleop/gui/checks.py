"""Pre-session connection checks for the collection page (log-040): PICO link, leader / follower CAN, cameras.

Each check is quick and read-only (it never moves or powers anything). The PICO input test reads the PICO for
1.5 s, so it only runs when no sender holds the single-reader lock (before ① or after ④).
Setup facts behind the defaults: docs/pico-working-setup.md (log-040).
"""
from __future__ import annotations

import dataclasses
import ipaddress
import pathlib
import re
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[2]
PICO_NET = ipaddress.ip_network("192.168.0.0/24")      # Wi-Fi `orboh` (PC 192.168.0.136, headset 192.168.0.233 on 11-01)
HEADSET_IP = "192.168.0.233"
SERVICE_PORT = 60061                                   # PICO PC Service (XRoboToolkit RoboticsServiceProcess), local SDK port
CAMERAS = {"chest": "344422070898", "left": "409122272149", "right": "352122273417"}   # librealsense serials
LEROBOT_PY = pathlib.Path("~/projects/lerobot/.venv/bin/python").expanduser()


@dataclasses.dataclass
class Check:
    key: str            # i18n key of the name
    ok: bool | None     # None = not checked now (e.g. busy)
    detail: str = ""
    fix: str = ""       # i18n key of what to do when it fails


def _run(argv: list[str], timeout: float = 3.0) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def wifi() -> Check:
    out = _run(["ip", "-4", "-br", "addr"])
    addrs = re.findall(r"(\d+\.\d+\.\d+\.\d+)/\d+", out)
    on = [a for a in addrs if ipaddress.ip_address(a) in PICO_NET]
    ssid = _run(["iwgetid", "-r"]).strip()
    return Check("chk.wifi", bool(on), f"{', '.join(on) or 'no 192.168.0.x address'}" + (f" · SSID {ssid}" if ssid else ""), "fix.wifi")


def service() -> Check:
    up = f":{SERVICE_PORT} " in _run(["ss", "-tln"]).replace(f":{SERVICE_PORT}\n", f":{SERVICE_PORT} \n")
    return Check("chk.service", up, f"127.0.0.1:{SERVICE_PORT}", "fix.service")


def headset(ip: str = HEADSET_IP) -> Check:
    out = _run(["ping", "-c", "1", "-W", "1", ip], timeout=2.5)
    m = re.search(r"time=([\d.]+) ms", out)
    return Check("chk.headset", bool(m), f"{ip}" + (f" · {m.group(1)} ms" if m else " · no answer"), "fix.headset")


def pico_input() -> Check:
    """1.5 s read of the PICO (scripts/check_input.sh). Needs the reader lock: not while a sender runs."""
    out = _run(["bash", str(REPO / "scripts" / "check_input.sh")], timeout=8.0)
    m = re.search(r"rate ~(\d+) Hz", out)
    if not m:
        return Check("chk.input", False, (out.strip() or "no output")[:120], "fix.input")
    hz = int(m.group(1))
    return Check("chk.input", hz >= 30, out.strip()[:140], "fix.input")


def can(name: str) -> Check:
    try:
        state = (pathlib.Path("/sys/class/net") / name / "operstate").read_text().strip()
    except OSError:
        state = "missing"
    return Check(f"chk.can.{name}", state in ("up", "unknown"), f"{name}: {state}", "fix.can")


def cameras(side: str) -> Check:
    want = {CAMERAS["chest"], CAMERAS[side]}
    out = _run([str(LEROBOT_PY), "-c", "import pyrealsense2 as rs\n"
                "print(' '.join(d.get_info(rs.camera_info.serial_number) for d in rs.context().query_devices()))"], timeout=8.0)
    seen = set(out.split())
    missing = want - seen
    return Check("chk.cameras", not missing, ("all found" if not missing else "missing " + ", ".join(sorted(missing))) +
                 f" (chest {CAMERAS['chest']}, {side} wrist {CAMERAS[side]})", "fix.cameras")


def no_demo() -> Check:
    """The PC Service's 3D demo window is a second PICO reader and steals the input (log-007)."""
    busy = bool(_run(["pgrep", "-af", "RobotLinuxDemo"]).strip())
    return Check("chk.demo", not busy, "RobotLinuxDemo " + ("running" if busy else "not running"), "fix.demo")


def quick(method: str, side: str) -> list[Check]:
    """The checks repeated every few seconds (cheap)."""
    out = []
    if method == "pico":
        out += [wifi(), service(), no_demo(), headset()]
    else:
        out.append(can(f"can_lead_{side[0]}"))
    out.append(can(f"can_fol_{side[0]}"))
    return out
