"""In-memory history for each sensor.

Two tiers keep memory small while still answering every question the cockpit
asks:

* fast: every sample at the analysis rate (default 10 minutes), used for live
  charts, vibration and spectrum analysis
* slow: one mean/min/max per second (default 3 hours), used for drift and
  long-window charts
"""

from __future__ import annotations

import numpy as np


class RingBuffer:
    """Fixed-capacity circular buffer of floats."""

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._cap = int(capacity)
        self._data = np.full(self._cap, np.nan, dtype=float)
        self._written = 0

    @property
    def available(self) -> int:
        return min(self._written, self._cap)

    def extend(self, values: np.ndarray) -> None:
        values = np.asarray(values, dtype=float)
        n = values.size
        if n == 0:
            return
        if n >= self._cap:
            self._written += n - self._cap
            values = values[-self._cap:]
            n = self._cap
        start = self._written % self._cap
        end = start + n
        if end <= self._cap:
            self._data[start:end] = values
        else:
            split = self._cap - start
            self._data[start:] = values[:split]
            self._data[: end - self._cap] = values[split:]
        self._written += n

    def last(self, n: int) -> np.ndarray:
        """The newest ``n`` values in chronological order (a copy)."""
        n = max(0, min(int(n), self.available))
        if n == 0:
            return np.empty(0, dtype=float)
        end = self._written % self._cap
        start = end - n
        if start >= 0:
            return self._data[start:end].copy()
        return np.concatenate((self._data[start:], self._data[:end]))


class SensorBuffer:
    """Fast full-rate history plus per-second aggregates for one sensor."""

    def __init__(self, rate_hz: int, fast_seconds: int, slow_seconds: int) -> None:
        self.rate_hz = int(rate_hz)
        self.dt = 1.0 / self.rate_hz
        self.fast = RingBuffer(self.rate_hz * int(fast_seconds))
        self.slow_t = RingBuffer(int(slow_seconds))
        self.slow_mean = RingBuffer(int(slow_seconds))
        self.slow_min = RingBuffer(int(slow_seconds))
        self.slow_max = RingBuffer(int(slow_seconds))
        self.t_last: float | None = None
        self._second: int | None = None
        self._pending: list[np.ndarray] = []

    @property
    def fast_seconds_available(self) -> float:
        return self.fast.available / self.rate_hz

    def append(self, values: np.ndarray, t_end: float) -> None:
        """Add consecutive samples whose newest sample was taken at ``t_end``."""
        values = np.asarray(values, dtype=float)
        if values.size == 0:
            return
        self.fast.extend(values)
        times = t_end - (values.size - 1 - np.arange(values.size)) * self.dt
        seconds = np.floor(times).astype(np.int64)
        cuts = np.flatnonzero(np.diff(seconds)) + 1
        for chunk_idx in np.split(np.arange(values.size), cuts):
            second = int(seconds[chunk_idx[0]])
            if self._second is None:
                self._second = second
            elif second != self._second:
                self._flush()
                self._second = second
            self._pending.append(values[chunk_idx])
        self.t_last = float(t_end)

    def _flush(self) -> None:
        if self._second is None or not self._pending:
            return
        block = np.concatenate(self._pending)
        ok = block[np.isfinite(block)]
        self.slow_t.extend(np.array([self._second + 0.5]))
        if ok.size:
            self.slow_mean.extend(np.array([ok.mean()]))
            self.slow_min.extend(np.array([ok.min()]))
            self.slow_max.extend(np.array([ok.max()]))
        else:
            for ring in (self.slow_mean, self.slow_min, self.slow_max):
                ring.extend(np.array([np.nan]))
        self._pending = []

    def fast_window(self, seconds: float) -> tuple[float, float, np.ndarray]:
        """(time of first sample, sample spacing, values) for the last ``seconds``."""
        values = self.fast.last(int(round(seconds * self.rate_hz)))
        t_last = self.t_last if self.t_last is not None else 0.0
        t0 = t_last - (values.size - 1) * self.dt if values.size else t_last
        return t0, self.dt, values

    def fast_range(self, t_start: float, t_end: float) -> tuple[float, float, np.ndarray]:
        """Samples between two timestamps, limited to what the fast buffer holds."""
        if self.t_last is None:
            return t_start, self.dt, np.empty(0)
        newest_offset = max(0.0, self.t_last - t_end)
        span = max(0.0, t_end - t_start)
        total = self.fast.last(int(round((newest_offset + span) * self.rate_hz)))
        drop_new = int(round(newest_offset * self.rate_hz))
        values = total[: total.size - drop_new] if drop_new else total
        t_last_kept = self.t_last - drop_new * self.dt
        t0 = t_last_kept - (values.size - 1) * self.dt if values.size else t_start
        return t0, self.dt, values

    def slow_window(self, seconds: float) -> dict[str, np.ndarray]:
        """Per-second aggregates covering the last ``seconds``."""
        n = int(seconds) + 1
        t = self.slow_t.last(n)
        data = {
            "t": t,
            "mean": self.slow_mean.last(n),
            "min": self.slow_min.last(n),
            "max": self.slow_max.last(n),
        }
        if self.t_last is not None and t.size:
            keep = t >= self.t_last - seconds
            data = {key: arr[keep] for key, arr in data.items()}
        return data
