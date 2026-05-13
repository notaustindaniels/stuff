"""CLI: render a scene.json to PNG frames and (optionally) an mp4."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Optional

from .camera_path import CameraPath
from .compose import estimate_element_pixel_size, render_frame
from .raster import RasterCache
from .scene import load_scene


def _parse_args(argv) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="svg_fpv_engine.render")
    p.add_argument("scene", help="path to scene.json")
    p.add_argument("--out", default=None, help="output mp4 path; omit to skip mp4")
    p.add_argument("--frames-dir", default=None, help="where to write PNG frames")
    p.add_argument("--start-frame", type=int, default=None)
    p.add_argument("--end-frame", type=int, default=None)
    p.add_argument("--png-only", action="store_true", help="don't assemble mp4")
    p.add_argument("--debug-overlay", action="store_true")
    return p.parse_args(argv)


def _ffmpeg_assemble(frames_dir: str, out_path: str, fps: int) -> None:
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"

    cmd = [
        ffmpeg, "-y",
        "-framerate", str(fps),
        "-i", os.path.join(frames_dir, "%06d.png"),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-crf", "18",
        "-movflags", "+faststart",
        out_path,
    ]
    subprocess.run(cmd, check=True)


def main(argv: Optional[list] = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    scene = load_scene(args.scene)
    print(f"[render] scene '{scene.scene_id}' {scene.render.width}x{scene.render.height} "
          f"@ {scene.render.fps}fps, dur={scene.render.duration_s}s, "
          f"{len(scene.elements)} elements")

    camera_path = CameraPath.from_keyframes(
        scene.camera.keyframes,
        ease=scene.camera.ease,
    )

    total_frames = int(round(scene.render.duration_s * scene.render.fps))
    if total_frames <= 0:
        print("nothing to render (duration_s * fps <= 0)", file=sys.stderr)
        return 2
    start = 0 if args.start_frame is None else args.start_frame
    end = total_frames - 1 if args.end_frame is None else args.end_frame
    start = max(0, min(start, total_frames - 1))
    end = max(start, min(end, total_frames - 1))

    # Rasterize each SVG once at a size matched to its max apparent footprint.
    cache = RasterCache()
    print("[render] rasterizing SVGs...")
    for el in scene.elements:
        max_px = estimate_element_pixel_size(el, camera_path, scene)
        cache.prepare(el, target_px_long_edge=max_px)
        print(f"  {el.id:<24} -> long edge {max_px}px")

    # Frames directory.
    cleanup_frames = False
    if args.frames_dir is None:
        frames_dir = tempfile.mkdtemp(prefix="svg_fpv_frames_")
        cleanup_frames = (not args.png_only) and (args.out is not None)
    else:
        frames_dir = args.frames_dir
        os.makedirs(frames_dir, exist_ok=True)

    print(f"[render] frames -> {frames_dir}")
    t_start = time.time()
    for fi in range(start, end + 1):
        img = render_frame(scene, camera_path, fi, cache, debug_overlay=args.debug_overlay)
        # Composite onto opaque background by dropping alpha.
        img.convert("RGB").save(os.path.join(frames_dir, f"{fi:06d}.png"))
        if (fi - start) % max(1, (end - start + 1) // 20) == 0:
            elapsed = time.time() - t_start
            done = fi - start + 1
            total = end - start + 1
            print(f"  frame {fi}/{end}  ({done}/{total})  {elapsed:.1f}s elapsed")
    print(f"[render] {end - start + 1} frames in {time.time() - t_start:.1f}s")

    if args.out and not args.png_only:
        print(f"[render] ffmpeg -> {args.out}")
        _ffmpeg_assemble(frames_dir, args.out, scene.render.fps)
        if cleanup_frames:
            shutil.rmtree(frames_dir, ignore_errors=True)
        print(f"[render] wrote {args.out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
