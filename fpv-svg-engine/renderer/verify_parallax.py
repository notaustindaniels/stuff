"""Parallax-differential sanity check.

Per element, compute the on-screen pixel trajectory over the clip from
projection.py, AND compute the analytic screen-velocity prediction from
the pinhole derivative formula. They must agree per-element (that proves
projection.py is consistent with the textbook pinhole equation).

For pure camera translation, the screen-velocity of a static world point is:
    d(sx)/dt = fx * ( -vx_cam / z_cam  +  vz_cam * x_cam / z_cam^2 )
    d(sy)/dt = fy * (  vy_cam / z_cam  -  vz_cam * y_cam / z_cam^2 )

Here vx/vy/vz are the camera-velocity components expressed in camera basis.
With our lake-scene camera moving purely along world +z and the look_at
also along +z (camera-fwd = world-z), this simplifies to vz_cam = +v_world,
vx_cam = vy_cam = 0, and the radial-expansion term dominates near small
z_cam. That is exactly the "speck-to-grow-to-pass-to-speck" geometry.

After per-element consistency is established, we also report the *spread*
of peak per-second screen motion across kinds. A correctly-grounded scene
shows orders-of-magnitude differential between near and far elements.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from projection import (
    camera_basis,
    fade_alpha,
    focal_pixels,
    lerp_keyframes,
    project_point,
)


def sample_camera(plan, t):
    cam_pos, look_at = lerp_keyframes(plan["camera"]["keyframes"], t)
    right, up, fwd = camera_basis(cam_pos, look_at)
    return cam_pos, right, up, fwd


def camera_velocity_world(plan, t, dt=1e-3):
    p0, _ = lerp_keyframes(plan["camera"]["keyframes"], max(0, t - dt))
    p1, _ = lerp_keyframes(plan["camera"]["keyframes"], t + dt)
    return (p1 - p0) / (2 * dt)


def analytic_screen_velocity(plan, element, t, fx, fy):
    """Analytic d(sx)/dt, d(sy)/dt for a static world point."""
    cam_pos, right, up, fwd = sample_camera(plan, t)
    rel = np.array(element["pos"], float) - cam_pos
    z_cam = float(rel @ fwd)
    if z_cam <= 0:
        return None
    x_cam = float(rel @ right)
    y_cam = float(rel @ up)
    v_world = camera_velocity_world(plan, t)
    vx_cam = float(v_world @ right)
    vy_cam = float(v_world @ up)
    vz_cam = float(v_world @ fwd)
    # Point moves relative to camera at -v_cam (camera moves +v means point
    # moves -v in camera frame). dx_cam/dt = -vx_cam, etc.
    # sx = fx * x_cam / z_cam  +  cx
    # d/dt(sx) = fx * ( d(x_cam)/dt / z_cam  -  x_cam * d(z_cam)/dt / z_cam^2 )
    #         = fx * ( -vx_cam / z_cam  -  x_cam * (-vz_cam) / z_cam^2 )
    #         = fx * ( -vx_cam / z_cam  +  vz_cam * x_cam / z_cam^2 )
    # sy = -fy * y_cam / z_cam + cy   (negative because screen-y goes down)
    # d/dt(sy) = -fy * ( -vy_cam / z_cam + vz_cam * y_cam / z_cam^2 )
    #         =  fy * (  vy_cam / z_cam - vz_cam * y_cam / z_cam^2 )
    dsx_dt = fx * (-vx_cam / z_cam + vz_cam * x_cam / z_cam**2)
    dsy_dt = fy * (vy_cam / z_cam - vz_cam * y_cam / z_cam**2)
    return dsx_dt, dsy_dt, z_cam


def trajectory(plan, element, fx, fy, cx, cy):
    fps = plan["render"]["fps"]
    duration = plan["render"]["duration_s"]
    n_frames = int(round(duration * fps)) + 1
    out = []
    near, far = element["fade"]["near_m"], element["fade"]["far_m"]
    for i in range(n_frames):
        t = i / fps
        cam_pos, right, up, fwd = sample_camera(plan, t)
        proj = project_point(element["pos"], cam_pos, right, up, fwd, fx, fy, cx, cy)
        if proj is None:
            continue
        sx, sy, z_cam = proj
        a = fade_alpha(z_cam, near, far)
        if a <= 0:
            continue
        out.append((t, sx, sy, z_cam, a))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--detail", action="store_true",
                    help="print per-element rows")
    args = ap.parse_args()
    plan = json.loads(Path(args.plan).read_text())
    w, h = plan["render"]["width"], plan["render"]["height"]
    fps = plan["render"]["fps"]
    fx, fy, cx, cy = focal_pixels(plan["render"]["fov_x_deg"], w, h)

    print(f"camera: fx={fx:.2f}  frame={w}x{h}  fps={fps}")
    print(f"keyframes: {plan['camera']['keyframes']}\n")

    # ---- Per-element consistency: analytic vs trajectory-derived ----
    print("Per-element consistency (analytic d(sx)/dt vs finite-difference):")
    print("-" * 90)
    print(f"{'#':>3}  {'kind':12s}  {'t*':>6s}  {'z*':>9s}  "
          f"{'|v|_analytic':>14s}  {'|v|_traj':>12s}  {'rel_err':>8s}")

    max_per_kind = {}   # kind -> peak |v| (px/s)
    z_at_peak = {}
    all_errors = []
    fails = 0

    for i, el in enumerate(plan["elements"]):
        traj = trajectory(plan, el, fx, fy, cx, cy)
        if len(traj) < 2:
            continue
        arr = np.array(traj)
        # Frame-to-frame screen speed (px/frame -> px/s).
        dsx = np.diff(arr[:, 1]) * fps
        dsy = np.diff(arr[:, 2]) * fps
        speed_traj = np.sqrt(dsx**2 + dsy**2)
        # Use the midpoint t for each pair; analytic at that t.
        t_mid = 0.5 * (arr[:-1, 0] + arr[1:, 0])
        speed_analytic = []
        for t in t_mid:
            r = analytic_screen_velocity(plan, el, float(t), fx, fy)
            speed_analytic.append(0.0 if r is None else np.hypot(r[0], r[1]))
        speed_analytic = np.array(speed_analytic)
        # Relative error at the peak-motion frame.
        k = int(np.argmax(speed_traj))
        v_t = speed_traj[k]
        v_a = speed_analytic[k]
        err = abs(v_t - v_a) / max(1e-6, v_a) * 100
        all_errors.append(err)
        if err > 5.0:
            fails += 1
        kind = el["kind"]
        z = float(arr[k, 3])
        if kind not in max_per_kind or v_t > max_per_kind[kind]:
            max_per_kind[kind] = v_t
            z_at_peak[kind] = z
        if args.detail or err > 5.0:
            print(f"{i:3d}  {kind:12s}  {t_mid[k]:6.3f}  {z:8.2f}m  "
                  f"{v_a:14.2f}  {v_t:12.2f}  {err:7.2f}%")

    n = len(all_errors)
    median_err = float(np.median(all_errors)) if n else 0.0
    print()
    print(f"consistency: median rel-err = {median_err:.3f}%   "
          f"elements with >5% err: {fails}/{n}")
    if median_err < 0.5 and fails == 0:
        print("PROJECTION MATH: consistent with analytic pinhole-derivative formula.")
    else:
        print("PROJECTION MATH: drift between projector and analytic prediction.")

    # ---- Differential spread across kinds ----
    print("\nParallax differential across kinds (peak px/s per kind):")
    print("-" * 90)
    rows = sorted(max_per_kind.items(), key=lambda kv: -kv[1])
    for kind, v in rows:
        print(f"  {kind:14s}  z@peak={z_at_peak[kind]:8.2f}m  peak v = {v:10.2f} px/s")
    # Pick the slowest kind with non-trivial (>0.01 px/s) motion for ratio.
    moving = [r for r in rows if r[1] > 0.01]
    if len(moving) >= 2:
        ratio = moving[0][1] / moving[-1][1]
        print(f"\n  fastest-vs-slowest-moving kind: {ratio:.0f}x velocity differential")
        print(f"  (the depth illusion lives in this differential)")


if __name__ == "__main__":
    main()
