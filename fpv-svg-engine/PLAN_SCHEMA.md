# Resolved-Plan JSON Schema

The renderer consumes a single JSON file that fully specifies a scene. The Math Resolver (future) will produce files of this shape from a sparser director-spec. The hand-authored `scenes/lake/plan.json` is the canonical reference.

All distances are meters. Coordinate convention: +x right, +y up, +z forward. Camera looks toward its forward direction (`look_at - pos`, normalized).

## Top level

```json
{
  "scene_id":  "lake_v1",
  "render":    { ... },
  "camera":    { "keyframes": [ ... ] },
  "elements":  [ { ... }, ... ]
}
```

## render

| field             | type      | meaning                                |
|-------------------|-----------|----------------------------------------|
| `width`           | int       | output width in pixels                 |
| `height`          | int       | output height in pixels                |
| `fps`             | int       | output frame rate                      |
| `duration_s`      | float     | clip length, seconds                   |
| `fov_x_deg`       | float     | horizontal field of view, degrees      |
| `near_m`          | float     | near plane in meters (cull behind)     |
| `background_rgb`  | [r,g,b]   | 0..255, fill for areas no billboard covers |

## camera

```json
"camera": {
  "keyframes": [
    { "t": 0.0, "pos": [0.0, 1.5, -10.0], "look_at": [0.0, 1.5, 100.0] },
    { "t": 5.0, "pos": [0.0, 1.5,  30.0], "look_at": [0.0, 1.5, 140.0] }
  ]
}
```

Keyframes are sampled by piecewise-linear interpolation of both `pos` and `look_at` in time. The camera's forward axis at time *t* is `normalize(look_at(t) - pos(t))`; the right axis is `cross(fwd, world_up)`; the up axis is `cross(right, fwd)`.

Two keyframes (start, end) produce constant velocity. More keyframes produce piecewise-linear motion. Future: cubic Bezier in log-velocity for the C¹-continuous start/stop curves the brief mentions (see `twolayer-quilt-deep.html`'s bloom curve).

## elements

Each element is one billboard *instance*. Many instances of the same kind share one SVG file (the Element Artist authors one SVG per kind).

```json
{
  "kind":     "wave_crest",
  "svg":      "svgs/wave_crest.svg",
  "pos":      [3.2, 0.10, 18.0],
  "size_m":   [0.60, 0.20],
  "fade":     { "near_m": 1.2, "far_m": 220 }
}
```

| field        | type         | meaning                                                |
|--------------|--------------|--------------------------------------------------------|
| `kind`       | string       | element kind tag (groups instances; cosmetic at render time, structural for the resolver/verifier) |
| `svg`        | relative path | path to canonical SVG, relative to plan.json's directory |
| `pos`        | [x, y, z]    | world position of the billboard center                  |
| `size_m`     | [w, h]       | physical billboard size in meters                       |
| `fade`       | { near_m, far_m } | alpha fades to 0 outside this z-band, with a band on each side |

### Billboard orientation

All billboards face the camera (their plane is perpendicular to the camera's forward axis). The projected pixel size is `(fx · w / z_cam, fy · h / z_cam)`. There is currently no `slant_z` for ground-perspective; that's flagged in the brief as a future feature.

### Drawing order

Each frame, elements are sorted back-to-front by `z_cam` and pasted with alpha onto the frame buffer, so nearer billboards occlude farther ones.

### Pre-rasterization

The renderer rasterizes each unique `svg` exactly *once* at the maximum width any instance of that kind will need across the clip (with 1.25× headroom, capped at 2400px). All per-frame draws are PIL `resize(LANCZOS)` of the cached raster. This implements the brief's "SVG sources rasterized once at high resolution rather than per-frame" discipline.

## What the Math Resolver must produce

Given a director-spec (extent, camera, zones, kinds), the Math Resolver computes:

1. Per-kind instance count from solid-angle × dwell-time math per depth band.
2. Per-instance `pos` and `size_m`, distributing within the kind's zone with the density required for visual continuity.
3. Per-instance `fade` bands, chosen so that the instance never flashes for <2-3 frames and stays large enough on-screen to read.
4. (Future) Per-kind required SVG authoring detail (max-projected-size hint for the Element Artist).

The output is exactly the file shape documented here. Today this file is written by hand; the resolver replaces that hand-authoring step.
