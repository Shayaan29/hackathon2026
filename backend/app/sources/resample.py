"""Bring a sensor's own sample rate to the analysis rate, whatever the ratio.

A fast sensor (20 kHz, 3.33 kHz, ...) is averaged down in blocks, which keeps the noise realistic and removes
aliasing. The blocks need not be a whole number of readings long: the position inside the stream is carried from
one call to the next, so no reading is lost or counted twice and the output keeps its rate over hours. A block
counts as invalid when more than half of its readings are invalid. A sensor slower than the analysis rate is held
for the right number of output samples.
"""

from __future__ import annotations

import numpy as np

_EPS = 1e-9


class Resampler:
    def __init__(self, analysis_hz: int) -> None:
        self.analysis_hz = int(analysis_hz)
        self._carry = np.empty(0)     # readings not used yet
        self._phase = 0.0             # where the next output block starts inside the carry, in readings (< 1)

    def reset(self) -> None:
        self._carry = np.empty(0)
        self._phase = 0.0

    def push(self, values: np.ndarray, sensor_hz: float) -> np.ndarray:
        data = np.concatenate((self._carry, np.asarray(values, dtype=float)))
        ratio = float(sensor_hz) / self.analysis_hz
        if not np.isfinite(ratio) or ratio <= 0:
            self._carry = data[-int(max(1.0, sensor_hz)):] if data.size else data
            return np.empty(0)
        if ratio >= 1.0:
            return self._average(data, ratio)
        return self._hold(data, ratio)

    def _average(self, data: np.ndarray, ratio: float) -> np.ndarray:
        n_out = int((data.size - self._phase) // ratio)
        if n_out <= 0:
            self._carry = data
            return np.empty(0)
        edges = np.floor(self._phase + np.arange(n_out + 1) * ratio + _EPS).astype(np.int64)
        edges = np.minimum(edges, data.size)
        used = int(edges[-1])
        block = data[:used]
        valid = np.isfinite(block)
        csum = np.concatenate(([0.0], np.cumsum(np.where(valid, block, 0.0))))
        ccount = np.concatenate(([0], np.cumsum(valid)))
        sums = csum[edges[1:]] - csum[edges[:-1]]
        counts = ccount[edges[1:]] - ccount[edges[:-1]]
        sizes = edges[1:] - edges[:-1]
        out = np.full(n_out, np.nan)
        good = counts * 2 > sizes
        out[good] = sums[good] / counts[good]
        self._phase = (self._phase + n_out * ratio) - used
        self._carry = data[used:]
        return out

    def _hold(self, data: np.ndarray, ratio: float) -> np.ndarray:
        if data.size == 0:
            return np.empty(0)
        n_out = int(np.ceil((data.size - self._phase) / ratio - _EPS))
        if n_out <= 0:
            self._phase -= data.size
            self._carry = np.empty(0)
            return np.empty(0)
        idx = np.floor(self._phase + np.arange(n_out) * ratio + _EPS).astype(np.int64)
        idx = np.clip(idx, 0, data.size - 1)
        out = data[idx]
        self._phase = max(0.0, self._phase + n_out * ratio - data.size)
        self._carry = np.empty(0)
        return out
