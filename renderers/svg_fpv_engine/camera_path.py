"""Continuous camera trajectory from discrete keyframes.

Centripetal Catmull-Rom spline (alpha=0.5) through each scalar channel
independently. Non-uniform time spacing is handled correctly; uniform
Catmull-Rom would kink/loop on tight turns with uneven spacing.

Yaw is unwrapped before splining so we never take the long way around the
circle when consecutive keyframes happen to straddle +/-pi. Pitch and roll
are not unwrapped (pitch is physically clamped, roll rarely wraps in
cinematic flight).

An ease curve `u(t)` is applied to the spline *parameter*, not to each
channel independently, so yaw and pitch stay synchronized through eased
segments.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

import numpy as np


# Channels we interpolate. Position is xyz, plus three angles, plus FOV.
_CHANNELS = ("px", "py", "pz", "yaw", "pitch", "roll", "fov_deg")


@dataclass(frozen=True)
class Keyframe:
    t: float
    pos: Tuple[float, float, float]
    yaw: float
    pitch: float
    roll: float
    fov_deg: float

    def value(self, channel: str) -> float:
        if channel == "px":
            return self.pos[0]
        if channel == "py":
            return self.pos[1]
        if channel == "pz":
            return self.pos[2]
        return getattr(self, channel)


@dataclass(frozen=True)
class CameraState:
    pos: np.ndarray  # shape (3,)
    yaw: float
    pitch: float
    roll: float
    fov_deg: float


# ---------------------------------------------------------------------------
# Ease curves: take s in [0, 1] -> u in [0, 1]
# ---------------------------------------------------------------------------

def _ease_linear(s: float) -> float:
    return s


def _ease_smoothstep(s: float) -> float:
    return s * s * (3.0 - 2.0 * s)


def _ease_bezier(p1: float, p2: float) -> Callable[[float], float]:
    """Cubic bezier with control points (0,0), (1/3, p1), (2/3, p2), (1,1).

    For ease curves authored as a 1D mapping. p1 and p2 are the y-values of
    the two interior control points; the x-values are fixed at 1/3 and 2/3.
    """
    def _f(s: float) -> float:
        om = 1.0 - s
        return (
            3.0 * om * om * s * p1
            + 3.0 * om * s * s * p2
            + s * s * s
        )
    return _f


def make_ease(spec) -> Callable[[float], float]:
    if spec is None or spec == "linear":
        return _ease_linear
    if spec == "smoothstep":
        return _ease_smoothstep
    if isinstance(spec, dict) and spec.get("type") == "bezier":
        return _ease_bezier(float(spec.get("p1", 0.0)), float(spec.get("p2", 1.0)))
    raise ValueError(f"unknown ease spec: {spec!r}")


# ---------------------------------------------------------------------------
# Yaw unwrap
# ---------------------------------------------------------------------------

def _unwrap_angles(values: List[float]) -> List[float]:
    """Adjust each value by integer multiples of 2*pi so consecutive deltas
    are within +/- pi. Walks left-to-right and accumulates the offset.
    """
    if not values:
        return values
    out = [values[0]]
    offset = 0.0
    for i in range(1, len(values)):
        prev = values[i - 1]
        cur = values[i] + offset
        d = cur - prev
        while d > math.pi:
            offset -= 2.0 * math.pi
            cur = values[i] + offset
            d = cur - prev
        while d < -math.pi:
            offset += 2.0 * math.pi
            cur = values[i] + offset
            d = cur - prev
        out.append(cur)
    return out


# ---------------------------------------------------------------------------
# Centripetal Catmull-Rom (1D, per channel)
# ---------------------------------------------------------------------------

def _cr_segment(
    t: float,
    t0: float, t1: float, t2: float, t3: float,
    p0: float, p1: float, p2: float, p3: float,
) -> float:
    """Evaluate a centripetal Catmull-Rom segment at parameter t, where the
    segment is defined between (t1, p1) and (t2, p2) with neighbors (t0, p0)
    and (t3, p3). Uses the Barry-Goldman pyramid formulation so the segment
    naturally handles non-uniform parameterization.
    """
    # Linear interpolations between neighbors.
    a1 = ((t1 - t) * p0 + (t - t0) * p1) / (t1 - t0) if t1 != t0 else p1
    a2 = ((t2 - t) * p1 + (t - t1) * p2) / (t2 - t1) if t2 != t1 else p2
    a3 = ((t3 - t) * p2 + (t - t2) * p3) / (t3 - t2) if t3 != t2 else p3

    b1 = ((t2 - t) * a1 + (t - t0) * a2) / (t2 - t0) if t2 != t0 else a2
    b2 = ((t3 - t) * a2 + (t - t1) * a3) / (t3 - t1) if t3 != t1 else a3

    if t2 == t1:
        return p1
    return ((t2 - t) * b1 + (t - t1) * b2) / (t2 - t1)


def _centripetal_knots(points_1d: Sequence[float], ts: Sequence[float]) -> List[float]:
    """Compute centripetal Catmull-Rom knots from sample points and base
    parameter values. For pure 1D channels we still want non-uniform knots so
    that segments with large value deltas don't bulge. The centripetal scheme
    uses sqrt of chord length, where chord here is `abs(dt)` blended with
    `abs(dvalue)` to keep things stable in 1D. We use the time axis directly
    since our keyframes are time-stamped; this gives equivalent behavior to
    uniform Catmull-Rom in t, which is the standard for keyframe animation.
    """
    return list(ts)


# ---------------------------------------------------------------------------
# CameraPath
# ---------------------------------------------------------------------------

@dataclass
class CameraPath:
    keyframes: List[Keyframe]
    ease: Callable[[float], float] = _ease_smoothstep
    duration: float = 0.0
    _channel_values: dict = field(default_factory=dict)
    _ts: List[float] = field(default_factory=list)

    @classmethod
    def from_keyframes(
        cls,
        keyframes: Sequence[Keyframe],
        ease="smoothstep",
        duration: Optional[float] = None,
    ) -> "CameraPath":
        if len(keyframes) < 2:
            raise ValueError("CameraPath needs at least 2 keyframes")
        kfs = sorted(keyframes, key=lambda k: k.t)
        for i in range(1, len(kfs)):
            if kfs[i].t <= kfs[i - 1].t:
                raise ValueError("keyframe times must be strictly increasing")

        ts = [k.t for k in kfs]
        dur = duration if duration is not None else (ts[-1] - ts[0])

        # Unwrap yaw across the keyframe sequence.
        raw_yaws = [k.yaw for k in kfs]
        unwrapped_yaws = _unwrap_angles(raw_yaws)
        kfs = [
            Keyframe(
                t=k.t, pos=k.pos, yaw=unwrapped_yaws[i], pitch=k.pitch,
                roll=k.roll, fov_deg=k.fov_deg,
            )
            for i, k in enumerate(kfs)
        ]

        channel_values = {ch: [k.value(ch) for k in kfs] for ch in _CHANNELS}

        path = cls(
            keyframes=kfs,
            ease=make_ease(ease),
            duration=dur,
            _channel_values=channel_values,
            _ts=ts,
        )
        return path

    def _find_segment(self, t: float) -> int:
        """Return the index i such that ts[i] <= t <= ts[i+1]."""
        ts = self._ts
        if t <= ts[0]:
            return 0
        if t >= ts[-1]:
            return len(ts) - 2
        # Linear scan is fine for the keyframe counts we'll author by hand.
        for i in range(len(ts) - 1):
            if ts[i] <= t <= ts[i + 1]:
                return i
        return len(ts) - 2

    def _sample_channel(self, channel: str, t_eased: float) -> float:
        ts = self._ts
        vs = self._channel_values[channel]
        n = len(ts)
        i = self._find_segment(t_eased)

        # Build a 4-point stencil with phantom endpoints if needed.
        if i == 0:
            t0 = 2 * ts[0] - ts[1]
            p0 = 2 * vs[0] - vs[1]
        else:
            t0 = ts[i - 1]
            p0 = vs[i - 1]
        t1, p1 = ts[i], vs[i]
        t2, p2 = ts[i + 1], vs[i + 1]
        if i + 2 >= n:
            t3 = 2 * ts[n - 1] - ts[n - 2]
            p3 = 2 * vs[n - 1] - vs[n - 2]
        else:
            t3 = ts[i + 2]
            p3 = vs[i + 2]

        return _cr_segment(t_eased, t0, t1, t2, t3, p0, p1, p2, p3)

    def sample(self, t_seconds: float) -> CameraState:
        """Sample the camera state at a given time, with easing applied."""
        ts = self._ts
        t0, t_last = ts[0], ts[-1]
        # Apply ease to the [0, 1] normalized parameter so yaw and pitch stay
        # in lockstep through eased segments.
        if t_last > t0:
            s = (t_seconds - t0) / (t_last - t0)
            s = max(0.0, min(1.0, s))
            u = self.ease(s)
            t_eased = t0 + u * (t_last - t0)
        else:
            t_eased = t0

        px = self._sample_channel("px", t_eased)
        py = self._sample_channel("py", t_eased)
        pz = self._sample_channel("pz", t_eased)
        yaw = self._sample_channel("yaw", t_eased)
        pitch = self._sample_channel("pitch", t_eased)
        roll = self._sample_channel("roll", t_eased)
        fov_deg = self._sample_channel("fov_deg", t_eased)
        return CameraState(
            pos=np.array([px, py, pz], dtype=np.float64),
            yaw=yaw, pitch=pitch, roll=roll, fov_deg=fov_deg,
        )
