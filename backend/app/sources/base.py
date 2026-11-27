"""The interface every data source implements.

A source delivers consecutive samples at the analysis rate. Invalid readings
(no target, out of range, too little light) are delivered as NaN, never
dropped, so time stays continuous.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import numpy as np

from ..config import SensorConfig


class SourceError(RuntimeError):
    """Raised when a source cannot start or read."""


class SensorSource(ABC):
    kind: str = "base"

    def __init__(self, cfg: SensorConfig, rate_hz: int) -> None:
        self.cfg = cfg
        self.rate_hz = int(rate_hz)

    def start(self) -> None:
        """Open connections or files. Raise SourceError when that fails."""

    def stop(self) -> None:
        """Release connections or files."""

    @abstractmethod
    def read(self, now: float) -> tuple[np.ndarray, float]:
        """Return the samples that arrived since the last call and the time of
        the newest one. Return an empty array when nothing is new."""

    def backfill(self, seconds: float, now: float) -> tuple[np.ndarray, float] | None:
        """Optional history to show at start-up. Real sources return None."""
        return None

    def status(self) -> dict[str, Any]:
        """What the cockpit shows about the connection. Only live sources have something to say."""
        return {}

    def apply_setting(self, name: str, value: Any) -> None:
        """Push a changed setting to the device. The simulator models a few."""

    def inject(self, kind: str, now: float, **params: Any) -> dict[str, Any]:
        """Demo events. Only the simulator supports them."""
        raise SourceError(f"{self.kind} sources do not support demo events")
