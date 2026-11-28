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



class UnavailableSource(SensorSource):
    """A sensor whose connection could not even be set up, for example a wrong address in the sensor file.

    It delivers nothing and says why, so the cockpit shows the problem instead of numbers. There is no dummy
    data behind it: every number on the screen comes from the real sensor."""

    kind = "unavailable"

    def __init__(self, cfg: SensorConfig, rate_hz: int, error: str) -> None:
        super().__init__(cfg, rate_hz)
        self.error = error

    def read(self, now: float) -> tuple[np.ndarray, float]:
        return np.empty(0), now

    def status(self) -> dict[str, Any]:
        port = self.cfg.source.get("port")
        return {"kind": self.cfg.source_type, "state": "offline", "message": self.error,
                "host": str(self.cfg.source.get("host") or ""),
                "port": int(port) if str(port or "").isdigit() else 0,
                "rate_hz": None, "article": None, "serial": None, "samples": 0, "invalid_percent": None,
                "last_status": None, "reference_mm": None}
