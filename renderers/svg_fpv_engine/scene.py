"""Scene schema: dataclasses + JSON load/validate."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Union

import numpy as np

from .camera_path import Keyframe


@dataclass(frozen=True)
class RenderConfig:
    width: int = 1280
    height: int = 720
    fps: int = 24
    duration_s: float = 8.0
    bg_color: str = "#000000"


@dataclass(frozen=True)
class CameraSpec:
    keyframes: List[Keyframe]
    ease: Union[str, dict] = "smoothstep"


@dataclass(frozen=True)
class Element:
    id: str
    svg_path: str  # absolute path to the SVG file on disk
    pos: Tuple[float, float, float]
    size_wh: Tuple[float, float]
    anchor_uv: Tuple[float, float]  # in [0, 1]^2; (0.5, 0.5)=center, (0.5, 1)=bottom
    slant_k: float
    near_fade_m: float


@dataclass(frozen=True)
class Scene:
    scene_id: str
    world_unit: str
    extent: dict
    render: RenderConfig
    camera: CameraSpec
    elements: List[Element]
    source_dir: str  # for resolving relative svg paths


def _anchor_to_uv(anchor) -> Tuple[float, float]:
    if anchor == "center":
        return (0.5, 0.5)
    if anchor == "bottom":
        return (0.5, 1.0)
    if anchor == "top":
        return (0.5, 0.0)
    if isinstance(anchor, (list, tuple)) and len(anchor) == 2:
        au, av = float(anchor[0]), float(anchor[1])
        if not (0.0 <= au <= 1.0 and 0.0 <= av <= 1.0):
            raise ValueError(f"anchor uv out of [0,1]: {anchor}")
        return (au, av)
    raise ValueError(f"unknown anchor: {anchor!r}")


def _resolve_size(size_field, svg_aspect: Optional[float]) -> Tuple[float, float]:
    """Resolve a size field into (width_m, height_m).

    Accepted forms:
      - [w, h] explicit
      - [w, "auto"] -> height derived from SVG aspect (w / aspect == h * aspect ??)
      - ["auto", h] -> width derived from SVG aspect
      - single number S -> [S, S/aspect] if aspect known else [S, S]
    Aspect is width/height of the SVG.
    """
    if isinstance(size_field, (int, float)):
        s = float(size_field)
        if svg_aspect is None:
            return (s, s)
        return (s, s / svg_aspect)
    if isinstance(size_field, (list, tuple)) and len(size_field) == 2:
        w_raw, h_raw = size_field
        w_auto = (w_raw == "auto")
        h_auto = (h_raw == "auto")
        if w_auto and h_auto:
            raise ValueError("size cannot have both dimensions auto")
        if w_auto:
            h = float(h_raw)
            if svg_aspect is None:
                return (h, h)
            return (h * svg_aspect, h)
        if h_auto:
            w = float(w_raw)
            if svg_aspect is None:
                return (w, w)
            return (w, w / svg_aspect)
        return (float(w_raw), float(h_raw))
    raise ValueError(f"unsupported size_m: {size_field!r}")


def _svg_aspect_ratio(path: str) -> Optional[float]:
    """Cheap-and-best-effort SVG aspect ratio (width / height) from viewBox or
    width/height attributes. Returns None if it can't be determined.
    """
    try:
        import xml.etree.ElementTree as ET
        tree = ET.parse(path)
        root = tree.getroot()
        vb = root.attrib.get("viewBox")
        if vb:
            parts = vb.replace(",", " ").split()
            if len(parts) == 4:
                _, _, w, h = (float(p) for p in parts)
                if h > 0:
                    return w / h
        w_attr = root.attrib.get("width")
        h_attr = root.attrib.get("height")
        if w_attr and h_attr:
            # Strip units like "px"
            def _num(s):
                num = ""
                for c in s:
                    if c.isdigit() or c in ".-":
                        num += c
                    else:
                        break
                return float(num) if num else None
            w = _num(w_attr)
            h = _num(h_attr)
            if w and h and h > 0:
                return w / h
    except Exception:
        return None
    return None


def load_scene(path: str) -> Scene:
    """Load + validate a scene.json. Resolves SVG paths relative to the JSON."""
    with open(path) as f:
        data = json.load(f)

    source_dir = os.path.dirname(os.path.abspath(path))

    r = data.get("render", {})
    render = RenderConfig(
        width=int(r.get("width", 1280)),
        height=int(r.get("height", 720)),
        fps=int(r.get("fps", 24)),
        duration_s=float(r.get("duration_s", 8.0)),
        bg_color=str(r.get("bg_color", "#000000")),
    )

    cam = data.get("camera", {})
    kf_specs = cam.get("keyframes", [])
    if len(kf_specs) < 2:
        raise ValueError("camera.keyframes needs at least 2 entries")
    keyframes = []
    for k in kf_specs:
        keyframes.append(Keyframe(
            t=float(k["t"]),
            pos=tuple(float(x) for x in k["pos"]),
            yaw=float(k.get("yaw", 0.0)),
            pitch=float(k.get("pitch", 0.0)),
            roll=float(k.get("roll", 0.0)),
            fov_deg=float(k.get("fov_deg", 65.0)),
        ))
    camera = CameraSpec(keyframes=keyframes, ease=cam.get("ease", "smoothstep"))

    elements: List[Element] = []
    for e in data.get("elements", []):
        svg_rel = e["svg"]
        svg_path = svg_rel if os.path.isabs(svg_rel) else os.path.join(source_dir, svg_rel)
        if not os.path.exists(svg_path):
            raise FileNotFoundError(f"SVG not found: {svg_path}")
        aspect = _svg_aspect_ratio(svg_path)
        size_wh = _resolve_size(e.get("size_m"), aspect)
        anchor_uv = _anchor_to_uv(e.get("anchor", "center"))
        elements.append(Element(
            id=str(e["id"]),
            svg_path=svg_path,
            pos=tuple(float(x) for x in e["pos"]),
            size_wh=size_wh,
            anchor_uv=anchor_uv,
            slant_k=float(e.get("slant_k", 0.0)),
            near_fade_m=float(e.get("near_fade_m", 0.0)),
        ))

    return Scene(
        scene_id=str(data.get("scene_id", "untitled")),
        world_unit=str(data.get("world_unit", "meter")),
        extent=data.get("extent", {}),
        render=render,
        camera=camera,
        elements=elements,
        source_dir=source_dir,
    )
