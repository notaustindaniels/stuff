# SVG FPV Drone Engine

Pipeline that renders short cinematic videos from SVG content placed as billboards in 3D space. See `../PROJECT_BRIEF.md` (the project brief uploaded in the originating session) for the full design rationale.

This repo currently implements **step 1+2+3** of the brief's "What I'd build first" plan:

1. **The renderer**, fed by a hand-authored "resolved plan" JSON.
2. **One scene end-to-end** (`scenes/lake/`), hand-authored: a sunset-lake FPV flythrough with wave crests in the foreground, mountains at the horizon, sunset clouds, and a backdrop sky/water billboard.
3. **A parallax-differential verifier** that proves projection.py implements the textbook pinhole derivative formula to 0.004% per-element error and reports the cross-kind velocity spread (~100,000× between foreground wave crests and far mountains).

The math-resolver (which will *produce* plans like this one automatically from a sparser director-spec) is intentionally left for later. Its specification falls out of `scenes/lake/plan.json`: that file's shape is what the resolver must emit.

## Quickstart

```bash
pip install Pillow numpy cairosvg     # ffmpeg from system
python3 renderer/render.py scenes/lake/plan.json --out out/lake
# -> out/lake/frames/f_*.png and out/lake/out.mp4

python3 renderer/verify_parallax.py scenes/lake/plan.json
```

## Why this approach (renderer first)

The whole project's depth-illusion claim — that *math-grounded* billboard placement reads as 3D parallax — was unproven until rendered frames existed. Building the math-resolver first would have produced inputs to a renderer that didn't exist, with the risk of either side being wrong invisibly. Building the renderer first against a hand-authored plan validates the load-bearing visual claim cheaply, then nails down the resolver's input/output spec.

## Layout

```
fpv-svg-engine/
  renderer/
    projection.py        # pinhole + camera basis + fade math (pure, testable)
    render.py            # per-frame loop: project, sort, paste, mp4
    verify_parallax.py   # analytic-vs-trajectory consistency check
  scenes/lake/
    plan.json            # hand-authored "resolved plan" for the lake scene
    svgs/                # canonical SVG per element kind
      backdrop.svg
      mountain.svg
      wave_crest.svg
      cloud.svg
  out/                   # generated frames + mp4 (gitignored)
  PLAN_SCHEMA.md         # the resolved-plan JSON shape
```

## Verifier output (current)

```
consistency: median rel-err = 0.004%   elements with >5% err: 0/29
PROJECTION MATH: consistent with analytic pinhole-derivative formula.

Parallax differential across kinds (peak px/s per kind):
  wave_crest      z@peak=    1.00m  peak v =   94959.09 px/s
  cloud           z@peak= 3270.33m  peak v =       1.53 px/s
  mountain        z@peak= 4770.33m  peak v =       0.95 px/s
  backdrop        z@peak= 9010.00m  peak v =       0.00 px/s

  fastest-vs-slowest-moving kind: 99665x velocity differential
```

The differential is the depth illusion. The visual proof is in `out/lake/out.mp4`: wave crests sweep dramatically through the foreground while mountains and backdrop stay essentially frozen.

## What this proves (and what it doesn't)

Proven:
- The pinhole-billboard approach can produce strong parallax differentials from flat SVGs alone, no 3D meshes.
- `projection.py` matches the analytic textbook formula (renderer is not introducing math drift).
- The "resolved plan" data shape is sufficient to drive a renderer.

Not yet proven / out of scope:
- Math Resolver (still hand-authored input).
- Continuity across cuts (single scene only).
- Within-element flatness fix (`slant_z` property or multi-billboard mountain).
- Performance at production scale.
- Element-artist agent loop, director agent, etc.

## Next concrete step

Read `scenes/lake/plan.json` and `PLAN_SCHEMA.md`. The Math Resolver's job is to take a sparser director-spec (extent, zones, camera, kinds) and emit a JSON of this exact shape — with element instance counts derived from depth-band solid-angle × dwell-time math.
