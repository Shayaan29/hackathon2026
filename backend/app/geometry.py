"""Where the sensors stand around the measured part. Shared by the engine, the
investigation and the 3D twin, so all three agree on the same positions."""

from __future__ import annotations

import math


def track_angle(index: int, count: int) -> float:
    """Where sensor ``index`` meets the turning part. The 3D twin uses the same rule:
    the first half of the sensors sit left of the axis, the rest right of it."""
    return math.pi if count > 1 and index < count / 2 else 0.0


def twin_side(index: int, count: int) -> str | None:
    """"left" or "right" as the twin draws it, or None when the configuration has no
    position for this sensor (a recording can carry more columns than the twin has slots)."""
    if index >= count:
        return None
    return "left" if track_angle(index, count) > 0 else "right"
