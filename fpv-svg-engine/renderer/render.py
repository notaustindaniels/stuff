"""SVG-billboard renderer.

Reads a resolved plan (positions/sizes/fades + camera keyframes), rasterizes
each kind's SVG once at the max size it will be drawn at across the whole
clip, then for each frame projects each instance, scales the cached raster,
and pastes onto the frame buffer in back-to-front order.

Outputs PNG frames into out/frames/ and an mp4 via ffmpeg.

Usage:
    python render.py scenes/lake/plan.json --out out/lake
"""

import argparse
import io
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import cairosvg
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from projection import (
    camera_basis,
    fade_alpha,
    focal_pixels,
    lerp_keyframes,
    project_point,
)


def rasterize_svg(svg_path: Path, target_width_px: int) -> Image.Image:
    """Rasterize an SVG to an RGBA PIL Image at the given width. Height
    follows the SVG's intrinsic aspect ratio."""
    png_bytes = cairosvg.svg2png(
        url=str(svg_path), output_width=int(target_width_px)
    )
    return Image.open(io.BytesIO(png_bytes)).convert("RGBA")


def max_projected_width_px(element, plan, fx) -> float:
    """Worst-case projected width in pixels for this element across the
    clip. Walks all sampled camera positions and takes the smallest z_cam
    that still has alpha > 0."""
    fps = plan["render"]["fps"]
    duration = plan["render"]["duration_s"]
    n_frames = int(round(duration * fps)) + 1
    near = element["fade"]["near_m"]
    far = element["fade"]["far_m"]
    size_x = element["size_m"][0]
    p_world = np.array(element["pos"], float)

    min_z = float("inf")
    for i in range(n_frames):
        t = i / fps
        cam_pos, look_at = lerp_keyframes(plan["camera"]["keyframes"], t)
        _, _, fwd = camera_basis(cam_pos, look_at)
        rel = p_world - cam_pos
        z_cam = float(rel @ fwd)
        if fade_alpha(z_cam, near, far) > 0 and z_cam < min_z:
            min_z = z_cam
    if not math.isfinite(min_z):
        # Never visible; use far as a fallback
        min_z = far
    return fx * size_x / max(0.1, min_z)


def prerasterize_kinds(plan, scene_dir: Path, fx) -> dict:
    """For each unique SVG path in the plan, rasterize once at the max width
    any instance will need (with a 1.25x headroom multiplier)."""
    by_svg = {}
    for el in plan["elements"]:
        svg = el["svg"]
        w = max_projected_width_px(el, plan, fx) * 1.25
        by_svg[svg] = max(by_svg.get(svg, 0.0), w)
    cache = {}
    for svg, w in by_svg.items():
        # Cap at a sane maximum so we don't allocate gigantic images for
        # near-camera elements with vanishing min_z.
        w_capped = int(min(max(32, w), 2400))
        path = scene_dir / svg
        print(f"  rasterize {svg} at width {w_capped}px", file=sys.stderr)
        cache[svg] = rasterize_svg(path, w_capped)
    return cache


def render_frame(plan, t, cache, frame_size, fx, fy, cx, cy, scene_dir):
    width, height = frame_size
    bg = tuple(plan["render"].get("background_rgb", [0, 0, 0])) + (255,)
    frame = Image.new("RGBA", (width, height), bg)

    cam_pos, look_at = lerp_keyframes(plan["camera"]["keyframes"], t)
    right, up, fwd = camera_basis(cam_pos, look_at)

    # Project each element; collect visible draws.
    draws = []
    for el in plan["elements"]:
        proj = project_point(el["pos"], cam_pos, right, up, fwd, fx, fy, cx, cy)
        if proj is None:
            continue
        sx, sy, z_cam = proj
        a = fade_alpha(z_cam, el["fade"]["near_m"], el["fade"]["far_m"])
        if a <= 0.001:
            continue
        size_x_m, size_y_m = el["size_m"]
        pw = fx * size_x_m / z_cam
        ph = fy * size_y_m / z_cam
        if pw < 0.5 or ph < 0.5:
            continue
        # Cheap frustum cull: skip if the billboard rect is fully off-screen.
        x0, y0 = sx - pw * 0.5, sy - ph * 0.5
        x1, y1 = sx + pw * 0.5, sy + ph * 0.5
        if x1 < 0 or x0 > width or y1 < 0 or y0 > height:
            continue
        draws.append((z_cam, sx, sy, pw, ph, a, el["svg"]))

    # Far → near so nearer billboards occlude.
    draws.sort(key=lambda d: -d[0])

    for z_cam, sx, sy, pw, ph, a, svg in draws:
        src = cache[svg]
        # Resize the cached raster down to the projected pixel size.
        tw = max(1, int(round(pw)))
        th = max(1, int(round(ph)))
        scaled = src.resize((tw, th), Image.LANCZOS)
        if a < 1.0:
            # Multiply the alpha channel.
            r, g, b, alpha = scaled.split()
            alpha = alpha.point(lambda v, a=a: int(v * a))
            scaled = Image.merge("RGBA", (r, g, b, alpha))
        x0 = int(round(sx - pw * 0.5))
        y0 = int(round(sy - ph * 0.5))
        frame.alpha_composite(scaled, dest=(x0, y0))
    return frame


def render_clip(plan_path: Path, out_dir: Path):
    plan = json.loads(plan_path.read_text())
    scene_dir = plan_path.parent
    w = plan["render"]["width"]
    h = plan["render"]["height"]
    fps = plan["render"]["fps"]
    duration = plan["render"]["duration_s"]
    n_frames = int(round(duration * fps))

    fx, fy, cx, cy = focal_pixels(plan["render"]["fov_x_deg"], w, h)
    print(f"camera: fx={fx:.2f} fov_x={plan['render']['fov_x_deg']}deg "
          f"frame={w}x{h} fps={fps} frames={n_frames}", file=sys.stderr)

    print("pre-rasterizing kinds...", file=sys.stderr)
    cache = prerasterize_kinds(plan, scene_dir, fx)

    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    print(f"rendering {n_frames} frames -> {frames_dir}", file=sys.stderr)
    for i in range(n_frames):
        t = i / fps
        frame = render_frame(plan, t, cache, (w, h), fx, fy, cx, cy, scene_dir)
        frame.convert("RGB").save(frames_dir / f"f_{i:05d}.png")
        if (i + 1) % 12 == 0 or i + 1 == n_frames:
            print(f"  frame {i+1}/{n_frames}", file=sys.stderr)

    mp4_path = out_dir / "out.mp4"
    print(f"encoding mp4 -> {mp4_path}", file=sys.stderr)
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-framerate", str(fps),
            "-i", str(frames_dir / "f_%05d.png"),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18",
            str(mp4_path),
        ],
        check=True,
    )
    print(f"done: {mp4_path}", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan", help="path to plan.json")
    ap.add_argument("--out", default="out/render", help="output directory")
    args = ap.parse_args()
    render_clip(Path(args.plan), Path(args.out))


if __name__ == "__main__":
    main()
