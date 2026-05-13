# svg_fpv_engine

Standalone renderer for the SVG FPV Drone Engine. Reads a `scene.json` plus a
folder of SVGs, projects each SVG as a billboard in 3D world space through a
pinhole camera, and writes PNG frames + an mp4.

## Install

```bash
cd renderers/svg_fpv_engine
pip install -r requirements.txt
```

The renderer is pure CPU Python: no WebGL, no browser. Long term it can be
swapped for the WebGL+canvas pattern from `twolayer-quilt-deep.html`; for v1
the math is easier to validate as a synchronous Python pipeline.

## Run

```bash
# from /home/user/stuff
python -m renderers.svg_fpv_engine.render \
    renderers/svg_fpv_engine/samples/scene_cabin.json \
    --out /tmp/out.mp4
```

Useful flags:

| flag | what it does |
| --- | --- |
| `--out FILE.mp4` | also assemble PNGs into an mp4 via ffmpeg |
| `--png-only` | skip the mp4 step (frames only) |
| `--frames-dir DIR` | write PNGs here (default: temp dir) |
| `--start-frame N` / `--end-frame M` | render only a range (fast iteration) |
| `--debug-overlay` | draw quad outlines + element ids + HUD |

## Architecture

### Coordinate frame

Right-handed, +X right, +Y up, **−Z forward**. Camera looks down −Z when
yaw=pitch=0. Forward dot is negated so `Zc > 0` means "in front of camera"
(graphics convention).

### Billboard projection

Each element is a **planar quad in world space**, not a screen-space sprite.
Its four world corners are projected independently through the pinhole camera
and the SVG is drawn into the resulting screen quad via a perspective
(homography) warp. This generalizes axis-aligned billboards (slant=0) and
slant-Z billboards (slant>0) into a single code path.

### Slant-Z

A billboard with `slant_k > 0` has its top edge tilted away from the camera in
the XZ plane, pivoting around its geometric bottom edge. This gives "ground
perspective" — a wide ridge or wall reads as a grounded slope rather than a
cardboard cutout — without ever introducing real 3D geometry. The slant
direction tracks the camera azimuth, so off-axis billboards stay anchored
correctly.

### Camera trajectory

Keyframes (time + position + yaw/pitch/roll + fov) are interpolated with a
centripetal Catmull-Rom spline per channel. Non-uniform `t` spacing is
handled correctly. Yaw is unwrapped before splining so we never take the long
way around the circle. Roll is **not** applied in the world→camera basis;
it's a 2D rotation of projected screen coordinates about screen center,
applied after projection.

An ease curve `u(t)` reparameterizes the spline (default: smoothstep). The
ease applies to the spline parameter, not per channel, so yaw and pitch stay
synchronized through eased segments.

### Near-fade & culling

If any corner of an element's projected quad has `Zc ≤ 0` (at or behind the
camera) the element is culled for that frame. Otherwise the element fades to
alpha=0 as its closest corner approaches `near_fade_m`. Proper near-plane
clipping in 3D is a v2 feature; for v1 just keep `near_fade_m` ≥ 1m.

### Painter order

Elements are sorted per-frame by the Zc of their projected centroid, back to
front. Limitation: two slanted billboards whose depth ranges overlap may paint
in the wrong order — avoid mutually-occluding slanted configurations, or
split long slanted elements into stacked sub-quads.

### Rasterization

Each unique SVG is rasterized once with `cairosvg`, at a pixel size matched to
its largest projected on-screen footprint across the entire trajectory
(×1.5 safety factor, capped at 2048 on the long edge). All per-frame warps
sample from that raster via Pillow's `Image.Transform.PERSPECTIVE`. No
mip-LOD for v1 — Pillow BICUBIC is acceptable at 720p for static cinematic
shots. Fast-moving small elements may shimmer; that's a v2 concern (precompute
2-3 mip levels, select by projected size).

## Scene schema

See `samples/scene_cabin.json` for a worked example. Minimal fields:

```json
{
  "scene_id": "...",
  "render": { "width": 1280, "height": 720, "fps": 24, "duration_s": 8.0, "bg_color": "#0b0a14" },
  "camera": {
    "keyframes": [
      { "t": 0.0, "pos": [0, 2, 30], "yaw": 0, "pitch": 0, "roll": 0, "fov_deg": 65 },
      ...
    ],
    "ease": "smoothstep"
  },
  "elements": [
    {
      "id": "cabin",
      "svg": "elements/cabin.svg",
      "pos": [0, 0, -40],
      "size_m": [12, 8],
      "anchor": "bottom",
      "slant_k": 0.0,
      "near_fade_m": 1.5
    }
  ]
}
```

`anchor` is `"center"`, `"bottom"`, `"top"`, or `[u, v]` in [0, 1]². `size_m`
can be `[w, h]`, `[w, "auto"]`, `["auto", h]`, or a single number.

## Known v1 limitations

- No mip-LOD on element rasters (shimmer on fast small motion)
- Painter order can mis-resolve overlapping depth ranges between slanted billboards
- Near-plane clipping is "cull the whole element" rather than per-pixel clip
- No motion blur, no fog, no depth-of-field
- No SVG animation (each SVG is rasterized once at load time)
