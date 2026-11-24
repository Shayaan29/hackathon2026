"""A loaded recording played through one of the cockpit's sensor slots.

The file plays at its own sample rate and starts again from the first sample
once the end is reached. The charts and the twin run on this playback. The
incidents themselves come from the whole-file analysis, not from this playback,
so a loop never creates duplicate alerts.
"""

from __future__ import annotations

import numpy as np

from ..config import SensorConfig
from .base import SensorSource


class RecordingSource(SensorSource):
    kind = "recording"

    def __init__(self, cfg: SensorConfig, rate_hz: int, values: np.ndarray, label: str = "") -> None:
        super().__init__(cfg, rate_hz)
        self._values = np.asarray(values, dtype=float)
        if self._values.size == 0:
            raise ValueError("a recording needs at least one sample")
        self.label = label
        self._cursor = 0
        self._t_last: float | None = None

    def start(self) -> None:
        self._cursor = 0
        self._t_last = None

    def _take(self, count: int) -> np.ndarray:
        out = np.empty(count, dtype=float)
        filled = 0
        size = self._values.size
        while filled < count:
            take = min(count - filled, size - self._cursor)
            out[filled:filled + take] = self._values[self._cursor:self._cursor + take]
            self._cursor = (self._cursor + take) % size
            filled += take
        return out

    def read(self, now: float) -> tuple[np.ndarray, float]:
        if self._t_last is None:
            self._t_last = now
            return np.empty(0), now
        due = int((now - self._t_last) * self.rate_hz)
        if due <= 0:
            return np.empty(0), self._t_last
        values = self._take(due)
        self._t_last += due / self.rate_hz
        return values, self._t_last

    def backfill(self, seconds: float, now: float) -> tuple[np.ndarray, float] | None:
        """The last ``seconds`` of the file, so the charts open on the end of the recording.
        Playback then continues from the first sample."""
        count = min(self._values.size, int(round(seconds * self.rate_hz)))
        tail = self._values[self._values.size - count:].copy()
        self._cursor = 0
        self._t_last = now
        return tail, now
