"""Pinhole-camera projection for camera-facing billboards.

Coordinates: +x right, +y up, +z forward. Camera looks toward its forward
direction (look_at - pos, normalized).

A billboard always faces the camera (its plane normal is the camera forward
direction reversed). Its projected pixel size is:
    px = (fx * size_x_m) / z_cam
    py = (fy * size_y_m) / z_cam
where z_cam is the depth of the billboard center along the camera forward axis.
"""

import math
import numpy as np


def lerp_keyframes(keyframes, t):
    """Piecewise-linear interpolation of camera keyframes. Each keyframe has
    fields: t (seconds), pos [3], look_at [3]. Clamps at the endpoints."""
    if t <= keyframes[0]["t"]:
        k = keyframes[0]
        return np.array(k["pos"], float), np.array(k["look_at"], float)
    if t >= keyframes[-1]["t"]:
        k = keyframes[-1]
        return np.array(k["pos"], float), np.array(k["look_at"], float)
    for a, b in zip(keyframes[:-1], keyframes[1:]):
        if a["t"] <= t <= b["t"]:
            u = (t - a["t"]) / (b["t"] - a["t"])
            pa, pb = np.array(a["pos"], float), np.array(b["pos"], float)
            la, lb = np.array(a["look_at"], float), np.array(b["look_at"], float)
            return pa + u * (pb - pa), la + u * (lb - la)
    raise RuntimeError("unreachable")


def camera_basis(pos, look_at, world_up=(0.0, 1.0, 0.0)):
    """Build orthonormal camera basis: right, up, forward (all unit vectors).
    Forward points from pos toward look_at."""
    fwd = np.asarray(look_at, float) - np.asarray(pos, float)
    n = np.linalg.norm(fwd)
    if n < 1e-9:
        raise ValueError("camera pos == look_at")
    fwd = fwd / n
    wu = np.asarray(world_up, float)
    right = np.cross(fwd, wu)
    rn = np.linalg.norm(right)
    if rn < 1e-9:
        # forward nearly parallel to world up; pick alternative
        right = np.cross(fwd, np.array([0.0, 0.0, 1.0]))
        rn = np.linalg.norm(right)
    right = right / rn
    up = np.cross(right, fwd)
    return right, up, fwd


def focal_pixels(fov_x_deg, width_px, height_px):
    """Return (fx, fy, cx, cy). We use square pixels: fx == fy, derived
    from horizontal FOV."""
    fx = (width_px * 0.5) / math.tan(math.radians(fov_x_deg) * 0.5)
    fy = fx
    cx = width_px * 0.5
    cy = height_px * 0.5
    return fx, fy, cx, cy


def project_point(p_world, cam_pos, right, up, fwd, fx, fy, cx, cy):
    """Project a 3D world point to (sx, sy, z_cam). Returns None if behind
    camera (z_cam <= 0)."""
    rel = np.asarray(p_world, float) - cam_pos
    x_cam = float(rel @ right)
    y_cam = float(rel @ up)
    z_cam = float(rel @ fwd)
    if z_cam <= 0:
        return None
    sx = fx * x_cam / z_cam + cx
    sy = -fy * y_cam / z_cam + cy
    return sx, sy, z_cam


def fade_alpha(z_cam, near_m, far_m):
    """Piecewise-linear fade. Full alpha in [near_m, far_m]; fades to 0 over
    a band on each side. Returns 0.0..1.0."""
    if z_cam <= 0:
        return 0.0
    band_near = max(0.25, near_m * 0.5)
    band_far = max(1.0, (far_m - near_m) * 0.10)
    z_lo = max(0.01, near_m - band_near)
    z_hi = far_m + band_far
    if z_cam < z_lo or z_cam > z_hi:
        return 0.0
    if z_cam < near_m:
        return (z_cam - z_lo) / (near_m - z_lo)
    if z_cam > far_m:
        return (z_hi - z_cam) / (z_hi - far_m)
    return 1.0
