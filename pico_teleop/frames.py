"""Frame conversion: OpenXR world (PICO) -> robot base frame.

OpenXR: +x right, +y up, +z toward the operator (so forward is -z).
Robot (URDF world, confirmed on the left arm in openarm-orboh ik/README.md): +x forward, +y left, +z up.
So robot = R_ROBOT_FROM_XR @ xr with x_r = -z_x, y_r = -x_x, z_r = y_x (same matrix as the draft).

This assumes the operator faces the robot's +x direction in XR space after recentering.
`operator_yaw_deg` rotates about robot +z when they face another way. Verify on gate 1/3 by
moving the controller forward/left/up and checking the sign of each robot axis.
"""
from __future__ import annotations

import math

import numpy as np

R_ROBOT_FROM_XR = np.array([[0.0, 0.0, -1.0], [-1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])


def quat_xyzw_to_mat(q) -> np.ndarray:
    x, y, z, w = (float(v) for v in q)
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n < 1e-9:
        raise ValueError("zero quaternion")
    x, y, z, w = x / n, y / n, z / n, w / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def yaw_mat(deg: float) -> np.ndarray:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def controller_in_robot(pose7, operator_yaw_deg: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """[x,y,z,qx,qy,qz,qw] in OpenXR -> (position, rotation) in the robot frame."""
    p = np.asarray(pose7, dtype=float)
    R = yaw_mat(operator_yaw_deg) @ R_ROBOT_FROM_XR
    return R @ p[:3], R @ quat_xyzw_to_mat(p[3:]) @ R.T
