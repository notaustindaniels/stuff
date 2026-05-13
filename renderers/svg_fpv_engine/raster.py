"""SVG -> PIL.Image rasterization, cached per element.

We rasterize each SVG once per render at a pixel size matched to its largest
expected on-screen footprint across the whole trajectory. PIL's BICUBIC
downsample in the per-frame homography handles the rest. No mip-LOD for v1.
"""

from __future__ import annotations

import io
import math
from typing import Dict

import cairosvg
import numpy as np
from PIL import Image

from .scene import Element


_MAX_LONG_EDGE = 2048
_SAFETY = 1.5


class RasterCache:
    def __init__(self) -> None:
        self._cache: Dict[str, Image.Image] = {}

    def get(self, element: Element) -> Image.Image:
        img = self._cache.get(element.id)
        if img is None:
            raise KeyError(f"raster for element '{element.id}' not prepared")
        return img

    def prepare(self, element: Element, target_px_long_edge: int) -> Image.Image:
        long_edge = int(min(_MAX_LONG_EDGE, max(64, math.ceil(target_px_long_edge * _SAFETY))))
        # Choose width or height as the long edge based on the element's
        # declared world size aspect.
        sw, sh = element.size_wh
        if sw >= sh:
            out_w = long_edge
            out_h = max(1, int(round(long_edge * sh / sw)))
        else:
            out_h = long_edge
            out_w = max(1, int(round(long_edge * sw / sh)))
        png_bytes = cairosvg.svg2png(
            url=element.svg_path,
            output_width=out_w,
            output_height=out_h,
        )
        img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        self._cache[element.id] = img
        return img
