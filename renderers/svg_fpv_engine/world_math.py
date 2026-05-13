"""Pinhole projection math for billboard SVGs in 3D world space.

Coordinate frame: right-handed, +X right, +Y up, -Z forward. The camera looks
down -Z when yaw=pitch=0. We negate the forward dot to get Zc>0 in front of
the camera (standard graphics convention).

Roll is NOT applied in the world->camera basis; it is applied as a 2D rotation
of projected screen-space coordinates about screen center, after projection.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence, Tuple

import numpy as np

Vec3 = Tuple[float, float, float]
ScreenPoint = Tuple[float, float]
Quad = Tuple[ScreenPoint, ScreenPoint, ScreenPoint, ScreenPoint]


# ---------------------------------------------------------------------------
# Camera basis
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Basis:
    right: np.ndarray  # shape (3,)
    up: np.ndarray
    fwd: np.ndarray


def build_basis(yaw: float, pitch: float) -> Basis:
    """Build the world->camera orthonormal basis from yaw and pitch.

    At yaw=pitch=0, fwd = (0, 0, -1), right = (1, 0, 0), up = (0, 1, 0).
    Yaw rotates about world +Y (rightward yaw = looking toward +X). Pitch
    rotates the forward vector up/down in the local-vertical plane.
    """
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    # Forward: yaw rotates -Z toward +X; pitch tilts up toward +Y.
    fwd = np.array([cp * sy, sp, -cp * cy], dtype=np.float64)
    # Right is yaw-rotated world horizontal, independent of pitch.
    right = np.array([cy, 0.0, sy], dtype=np.float64)
    # Up is reconstructed orthonormally. right x fwd points along world +Y
    # at yaw=pitch=0 (sanity-checked).
    up = np.cross(right, fwd)
    up /= np.linalg.norm(up)
    return Basis(right=right, up=up, fwd=fwd)


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------

def focal_px(width: int, fov_h_deg: float) -> float:
    return (width / 2.0) / math.tan(math.radians(fov_h_deg) / 2.0)


def world_to_cam(point: np.ndarray, cam_pos: np.ndarray, basis: Basis) -> Tuple[float, float, float]:
    """Return (Xc, Yc, Zc) where Zc > 0 means in front of camera.

    `fwd` points into the scene (the direction the camera looks). For a point
    in front of the camera, (point - cam_pos) and fwd are parallel-ish, so
    delta dot fwd is positive — that's Zc.
    """
    delta = point - cam_pos
    xc = float(delta @ basis.right)
    yc = float(delta @ basis.up)
    zc = float(delta @ basis.fwd)
    return xc, yc, zc


def project(xc: float, yc: float, zc: float, f_px: float) -> ScreenPoint:
    """Project a camera-space point to screen offset (origin = screen center).

    Assumes zc > 0; callers must cull points at or behind the camera.
    """
    sx = f_px * xc / zc
    sy = f_px * yc / zc
    return sx, sy


def apply_roll(sx: float, sy: float, roll: float) -> ScreenPoint:
    """Rotate a screen-space offset (about origin) by `roll` radians."""
    c, s = math.cos(roll), math.sin(roll)
    return c * sx - s * sy, s * sx + c * sy


def to_pixels(sx: float, sy: float, width: int, height: int) -> ScreenPoint:
    """Convert screen-space offset to pixel coordinates.

    World +Y maps to upper screen, so pixel_y is flipped.
    """
    return width / 2.0 + sx, height / 2.0 - sy


# ---------------------------------------------------------------------------
# Slant-Z billboard quad
# ---------------------------------------------------------------------------

def slant_quad_corners(
    pos: np.ndarray,
    size_wh: Tuple[float, float],
    anchor_uv: Tuple[float, float],
    slant_k: float,
    cam_pos: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return four world-space corners of a billboard quad: (TL, TR, BR, BL).

    The quad's right edge is yaw-aligned to face the camera in the XZ plane.
    The up axis tilts away from the camera in the XZ plane by `slant_k`
    radians. The slant pivot is always the geometric bottom edge of the quad,
    even when anchor is "center" (so `pos` positions the artwork but the
    physical tilt is grounded).
    """
    sw, sh = size_wh
    au, av = anchor_uv

    # Horizontal toward-camera direction, projected onto the XZ plane.
    dx = cam_pos[0] - pos[0]
    dz = cam_pos[2] - pos[2]
    n = math.hypot(dx, dz)
    if n < 1e-9:
        # Camera directly above/below pos: fall back to world +X for stability.
        to_cam_xz = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    else:
        to_cam_xz = np.array([dx / n, 0.0, dz / n], dtype=np.float64)

    world_up = np.array([0.0, 1.0, 0.0], dtype=np.float64)

    # Right vector: perpendicular to camera azimuth in the XZ plane.
    right_w = np.cross(world_up, to_cam_xz)
    rn = np.linalg.norm(right_w)
    if rn < 1e-9:
        right_w = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    else:
        right_w /= rn

    # Tilted up vector: pivots top edge away from camera in XZ.
    up_w = math.cos(slant_k) * world_up + math.sin(slant_k) * (-to_cam_xz)

    # Locate the bottom-edge midpoint regardless of anchor.
    # Anchor (au, av) in [0,1]^2 with (0,0)=top-left, (1,1)=bottom-right of the
    # unslanted artwork. A point at (u, v) in the artwork's UV maps to world:
    #   point(u, v) = bottom_mid + (u - 0.5)*sw*right_w + (1 - v)*sh*world_up
    # Setting point(au, av) = pos and solving:
    #   bottom_mid = pos - (au - 0.5)*sw*right_w - (1 - av)*sh*world_up
    # We offset along world_up (not the tilted up_w) so the artwork's "ground"
    # stays on the actual ground when slant is applied later.
    bottom_mid = (
        pos
        - (au - 0.5) * sw * right_w
        - (1.0 - av) * sh * world_up
    )

    bl = bottom_mid - (sw / 2.0) * right_w
    br = bottom_mid + (sw / 2.0) * right_w
    tl = bl + sh * up_w
    tr = br + sh * up_w
    return tl, tr, br, bl


# ---------------------------------------------------------------------------
# Homography coefficients for PIL's PERSPECTIVE transform
# ---------------------------------------------------------------------------

def perspective_coeffs(
    src_quad: Sequence[Tuple[float, float]],
    dst_quad: Sequence[Tuple[float, float]],
) -> Tuple[float, ...]:
    """Compute 8 coefficients mapping dst (x, y) -> src (x', y').

    PIL's Image.transform(..., PERSPECTIVE, coeffs) expects the inverse map
    (output pixel -> input pixel). Pass `src_quad` = the four corners of the
    source image you want sampled, and `dst_quad` = where each of those
    corners should appear in the output image. Order must match.
    """
    if len(src_quad) != 4 or len(dst_quad) != 4:
        raise ValueError("Both quads must have exactly 4 points")
    a = np.zeros((8, 8), dtype=np.float64)
    b = np.zeros(8, dtype=np.float64)
    for i, ((xs, ys), (xd, yd)) in enumerate(zip(src_quad, dst_quad)):
        a[2 * i] = [xd, yd, 1, 0, 0, 0, -xs * xd, -xs * yd]
        a[2 * i + 1] = [0, 0, 0, xd, yd, 1, -ys * xd, -ys * yd]
        b[2 * i] = xs
        b[2 * i + 1] = ys
    coeffs = np.linalg.solve(a, b)
    return tuple(float(c) for c in coeffs)


# ---------------------------------------------------------------------------
# Near-fade
# ---------------------------------------------------------------------------

def near_fade_alpha(d_min: float, near_fade_m: float) -> float:
    """Linear ramp: alpha=0 at d_min == near_fade_m, alpha=1 at 1.5x that."""
    if near_fade_m <= 0:
        return 1.0
    lo = near_fade_m
    hi = 1.5 * near_fade_m
    if d_min <= lo:
        return 0.0
    if d_min >= hi:
        return 1.0
    return (d_min - lo) / (hi - lo)
