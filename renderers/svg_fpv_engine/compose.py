"""Per-frame composition: sample camera, project all elements, paint."""

from __future__ import annotations

import math
from typing import List, Tuple

import numpy as np
from PIL import Image, ImageDraw

from .camera_path import CameraPath, CameraState
from .raster import RasterCache
from .scene import Element, Scene
from . import world_math as wm


def _project_corners(
    corners_world: List[np.ndarray],
    cam: CameraState,
    basis: wm.Basis,
    f_px: float,
    width: int,
    height: int,
) -> Tuple[List[Tuple[float, float]], List[float]]:
    """Project 4 world corners -> 4 pixel-space points + 4 Zc depths.

    Returns (pixel_quad, zc_list) where pixel_quad is in PIL coordinates
    (origin top-left). If any Zc <= 0 we still return the data; the caller
    decides whether to cull.
    """
    pixel_quad: List[Tuple[float, float]] = []
    zcs: List[float] = []
    for cw in corners_world:
        xc, yc, zc = wm.world_to_cam(cw, cam.pos, basis)
        zcs.append(zc)
        if zc <= 0:
            pixel_quad.append((float("nan"), float("nan")))
            continue
        sx, sy = wm.project(xc, yc, zc, f_px)
        sx, sy = wm.apply_roll(sx, sy, cam.roll)
        px, py = wm.to_pixels(sx, sy, width, height)
        pixel_quad.append((px, py))
    return pixel_quad, zcs


def _paint_quad(
    frame: Image.Image,
    src: Image.Image,
    pixel_quad: List[Tuple[float, float]],
    alpha: float,
) -> None:
    """Warp `src` so its four corners (TL, TR, BR, BL in source pixels) land
    at `pixel_quad` (same order) in `frame`, with overall multiplicative alpha.

    Renders into a tight bbox around the destination quad to avoid 8 MB
    full-frame allocations per element.
    """
    W, H = frame.size
    xs = [p[0] for p in pixel_quad]
    ys = [p[1] for p in pixel_quad]
    bbox_x0 = math.floor(min(xs))
    bbox_y0 = math.floor(min(ys))
    bbox_x1 = math.ceil(max(xs))
    bbox_y1 = math.ceil(max(ys))

    # Clip bbox to frame; if entirely outside, skip.
    clip_x0 = max(0, bbox_x0)
    clip_y0 = max(0, bbox_y0)
    clip_x1 = min(W, bbox_x1)
    clip_y1 = min(H, bbox_y1)
    if clip_x1 <= clip_x0 or clip_y1 <= clip_y0:
        return

    out_w = bbox_x1 - bbox_x0
    out_h = bbox_y1 - bbox_y0
    if out_w <= 0 or out_h <= 0:
        return

    # Destination corners in bbox-local coords.
    dst_quad = [(x - bbox_x0, y - bbox_y0) for (x, y) in pixel_quad]

    # Source corners in SVG raster pixel coords (TL, TR, BR, BL).
    sw, sh = src.size
    src_quad = [(0.0, 0.0), (sw, 0.0), (sw, sh), (0.0, sh)]

    try:
        coeffs = wm.perspective_coeffs(src_quad, dst_quad)
    except np.linalg.LinAlgError:
        return  # degenerate quad

    warped = src.transform(
        (out_w, out_h),
        Image.Transform.PERSPECTIVE,
        coeffs,
        resample=Image.Resampling.BICUBIC,
    )

    if alpha < 0.999:
        a = warped.getchannel("A")
        a = a.point(lambda v, k=alpha: int(v * k))
        warped.putalpha(a)

    # Alpha-composite into the frame at (bbox_x0, bbox_y0).
    frame.alpha_composite(warped, dest=(bbox_x0, bbox_y0))


def estimate_element_pixel_size(
    element: Element,
    camera_path: CameraPath,
    scene: Scene,
    samples: int = 0,
) -> int:
    """Sample the camera trajectory and return the maximum apparent pixel
    size this element ever achieves (long edge). Used to choose rasterization
    resolution.
    """
    fps = scene.render.fps
    n = samples if samples > 0 else int(scene.render.duration_s * fps) + 1
    W, H = scene.render.width, scene.render.height
    max_long_edge = 32
    pos = np.array(element.pos, dtype=np.float64)

    for i in range(n):
        t = scene.camera.keyframes[0].t + (scene.render.duration_s) * (i / max(1, n - 1))
        cam = camera_path.sample(t)
        basis = wm.build_basis(cam.yaw, cam.pitch)
        f_px = wm.focal_px(W, cam.fov_deg)

        corners = wm.slant_quad_corners(
            pos=pos,
            size_wh=element.size_wh,
            anchor_uv=element.anchor_uv,
            slant_k=element.slant_k,
            cam_pos=cam.pos,
        )
        pixel_quad, zcs = _project_corners(list(corners), cam, basis, f_px, W, H)
        if any(z <= 0 for z in zcs):
            continue
        xs = [p[0] for p in pixel_quad]
        ys = [p[1] for p in pixel_quad]
        dx = max(xs) - min(xs)
        dy = max(ys) - min(ys)
        max_long_edge = max(max_long_edge, int(math.ceil(max(dx, dy))))

    return max_long_edge


def render_frame(
    scene: Scene,
    camera_path: CameraPath,
    frame_idx: int,
    raster_cache: RasterCache,
    debug_overlay: bool = False,
) -> Image.Image:
    W = scene.render.width
    H = scene.render.height
    fps = scene.render.fps
    t0 = scene.camera.keyframes[0].t
    t = t0 + frame_idx / fps
    cam = camera_path.sample(t)
    basis = wm.build_basis(cam.yaw, cam.pitch)
    f_px = wm.focal_px(W, cam.fov_deg)

    frame = Image.new("RGBA", (W, H), scene.render.bg_color)

    drawables = []
    for el in scene.elements:
        pos = np.array(el.pos, dtype=np.float64)
        corners_world = wm.slant_quad_corners(
            pos=pos,
            size_wh=el.size_wh,
            anchor_uv=el.anchor_uv,
            slant_k=el.slant_k,
            cam_pos=cam.pos,
        )
        pixel_quad, zcs = _project_corners(
            list(corners_world), cam, basis, f_px, W, H,
        )
        if any(z <= 0 for z in zcs):
            continue  # cull: any corner at or behind camera
        d_min = min(zcs)
        d_centroid = sum(zcs) / 4.0
        alpha = wm.near_fade_alpha(d_min, el.near_fade_m)
        if alpha <= 0.0:
            continue
        drawables.append((d_centroid, el, pixel_quad, alpha, zcs))

    # Far first (largest Zc first) so near objects paint on top.
    drawables.sort(key=lambda d: -d[0])

    for _, el, pixel_quad, alpha, _zcs in drawables:
        src = raster_cache.get(el)
        _paint_quad(frame, src, pixel_quad, alpha)

    if debug_overlay:
        draw = ImageDraw.Draw(frame)
        for d_centroid, el, pixel_quad, alpha, zcs in drawables:
            # Outline the quad and label with id + d_centroid.
            pts = [(int(x), int(y)) for (x, y) in pixel_quad]
            draw.line(pts + [pts[0]], fill=(255, 220, 100, 220), width=1)
            label = f"{el.id}  d={d_centroid:.1f}  a={alpha:.2f}"
            draw.text((pts[0][0] + 2, pts[0][1] + 2), label, fill=(255, 240, 200, 255))
        # HUD: time, camera state
        hud = (
            f"t={t:.2f}s  pos=({cam.pos[0]:+.1f},{cam.pos[1]:+.1f},{cam.pos[2]:+.1f})  "
            f"yaw={cam.yaw:+.2f} pitch={cam.pitch:+.2f} roll={cam.roll:+.2f} fov={cam.fov_deg:.1f}"
        )
        draw.text((6, 6), hud, fill=(255, 220, 100, 255))

    return frame
