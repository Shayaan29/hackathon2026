"""Simulated sensor data for the automatic tests. The cockpit itself never shows simulated data.

The signal is built from the parts a real measurement shows:

* white measurement noise
* a slow, bounded wander (temperature, air movement)
* a small machine hum at a fixed frequency
* events a test triggers: a damped knock, a steady drift, a noise burst, a dropout with invalid
  readings and a dent on the turning part
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.config import SensorConfig
from app.sources.base import SensorSource, SourceError


@dataclass
class _Burst:
    t0: float
    amplitude: float
    frequency_hz: float
    decay_s: float

    @property
    def t_end(self) -> float:
        return self.t0 + 6.0 * self.decay_s


class SimulatedSensor(SensorSource):
    kind = "simulator"
    # the measured part turns at this speed (radians per second); set by the engine
    part_omega: float = 2 * math.pi * 8.6 / 60

    def __init__(self, cfg: SensorConfig, rate_hz: int) -> None:
        super().__init__(cfg, rate_hz)
        opts = cfg.source
        self.rng = np.random.default_rng(int(opts.get("seed", abs(hash(cfg.id)) % 10_000)))
        self.noise = float(opts.get("noise", 0.8))
        self.hum_hz = float(opts.get("hum_hz", 12.5))
        self.hum_amplitude = float(opts.get("hum_amplitude", 0.15))
        self.wander_amplitude = float(opts.get("wander_amplitude", 0.3))
        self.wander_periods = (110.0 + self.rng.uniform(0, 40), 260.0 + self.rng.uniform(0, 80))
        self.wander_phases = tuple(self.rng.uniform(0, 2 * math.pi, size=2))
        self._averaging_reference = None
        self._averaging = None
        self._t_last: float | None = None
        self._bursts: list[_Burst] = []
        self._drift_rate_per_s = 0.0
        self._drift_t0: float | None = None
        self._drift_offset = 0.0
        self._noise_factor = 1.0
        self._noise_until = 0.0
        self._dropout_share = 0.0
        self._dropout_until = 0.0
        # where this laser meets the turning part (set by the engine), its runout,
        # and an optional dent on the part (demo)
        self.track_angle = 0.0
        self.runout = float(opts.get("runout", 0.35))
        self._runout_phase = float(self.rng.uniform(0, 2 * math.pi))
        self._dent_depth = 0.0
        self._dent_angle = 0.0
        self._dent_width = math.radians(40.0)

    # -- signal model -------------------------------------------------------

    def _noise_scale(self) -> float:
        if not self._averaging_reference or not self._averaging:
            return 1.0
        return float(np.clip(math.sqrt(self._averaging_reference / self._averaging), 0.15, 4.0))

    def _signal(self, t: np.ndarray) -> np.ndarray:
        n = t.size
        values = np.full(n, self.cfg.nominal, dtype=float)

        sigma = self.noise * self._noise_scale()
        boost = np.where(t < self._noise_until, self._noise_factor, 1.0)
        values += sigma * boost * self.rng.standard_normal(n)

        (p1, p2), (f1, f2) = self.wander_periods, self.wander_phases
        values += self.wander_amplitude * (
            0.7 * np.sin(2 * math.pi * t / p1 + f1) + 0.3 * np.sin(2 * math.pi * t / p2 + f2)
        )
        values += self.hum_amplitude * np.sin(2 * math.pi * self.hum_hz * t)
        values += self._rotation(t)

        values += self._drift_offset
        if self._drift_t0 is not None:
            values += self._drift_rate_per_s * np.clip(t - self._drift_t0, 0.0, None)

        for burst in self._bursts:
            active = (t >= burst.t0) & (t < burst.t_end)
            if active.any():
                dt = t[active] - burst.t0
                values[active] += (
                    burst.amplitude
                    * np.exp(-dt / burst.decay_s)
                    * np.sin(2 * math.pi * burst.frequency_hz * dt)
                )

        if self._dropout_share > 0:
            drop = (t < self._dropout_until) & (self.rng.random(n) < self._dropout_share)
            values[drop] = np.nan
        return values

    def part_angle(self, t: np.ndarray) -> np.ndarray:
        """Angle on the part (part coordinates) that sits under this laser at time t."""
        return self.track_angle - self.part_omega * t

    def _rotation(self, t: np.ndarray) -> np.ndarray:
        """Runout of the turning part, plus the dent once per turn."""
        if self.part_omega <= 0:
            return np.zeros_like(t)
        phi = self.part_angle(t)
        out = self.runout * np.cos(phi - self._runout_phase)
        if self._dent_depth:
            d = (phi - self._dent_angle + math.pi) % (2 * math.pi) - math.pi
            x = np.abs(d) / (self._dent_width / 2)
            # smooth raised-cosine shape: a dent, not a knock
            out = out + self._dent_depth * np.where(x < 1.0, 0.5 + 0.5 * np.cos(math.pi * np.clip(x, 0, 1)), 0.0)
        return out

    def wander(self, t: np.ndarray) -> np.ndarray:
        (p1, p2), (f1, f2) = self.wander_periods, self.wander_phases
        return self.wander_amplitude * (0.7 * np.sin(2 * math.pi * t / p1 + f1)
                                        + 0.3 * np.sin(2 * math.pi * t / p2 + f2))

    # -- SensorSource -------------------------------------------------------

    def read(self, now: float) -> tuple[np.ndarray, float]:
        if self._t_last is None:
            self._t_last = now
            return np.empty(0), now
        n = int((now - self._t_last) * self.rate_hz)
        if n <= 0:
            return np.empty(0), self._t_last
        t = self._t_last + np.arange(1, n + 1) / self.rate_hz
        self._t_last = float(t[-1])
        values = self._signal(t)
        self._bursts = [b for b in self._bursts if b.t_end > self._t_last]
        return values, self._t_last

    def backfill(self, seconds: float, now: float) -> tuple[np.ndarray, float] | None:
        n = int(seconds * self.rate_hz)
        if n <= 0:
            return None
        t = now - (n - 1 - np.arange(n)) / self.rate_hz
        self._t_last = float(t[-1])
        return self._signal(t), self._t_last

    def inject(self, kind: str, now: float, **params: Any) -> dict[str, Any]:
        if kind == "vibration":
            burst = _Burst(
                t0=now + float(params.get("delay_s", 0.0)),
                amplitude=float(params.get("amplitude", 15.0)),
                frequency_hz=float(params.get("frequency_hz", 30.0)),
                decay_s=float(params.get("decay_s", 0.35)),
            )
            self._bursts.append(burst)
            return {"amplitude": burst.amplitude, "frequency_hz": burst.frequency_hz}
        if kind == "drift":
            self._stop_drift(now)
            self._drift_rate_per_s = float(params.get("rate_per_min", 6.0)) / 60.0
            self._drift_t0 = now
            return {"rate_per_min": self._drift_rate_per_s * 60.0}
        if kind == "drift_stop":
            self._stop_drift(now)
            return {"offset": self._drift_offset}
        if kind == "noise":
            self._noise_factor = float(params.get("factor", 4.0))
            self._noise_until = now + float(params.get("duration_s", 6.0))
            return {"factor": self._noise_factor, "until": self._noise_until}
        if kind == "dropout":
            self._dropout_share = float(params.get("share", 0.5))
            self._dropout_until = now + float(params.get("duration_s", 3.0))
            return {"share": self._dropout_share, "until": self._dropout_until}
        if kind == "dent":
            self._dent_depth = float(params.get("depth", 62.0))
            self._dent_angle = math.radians(float(params.get("angle_deg", 60.0)))
            self._dent_width = math.radians(float(params.get("width_deg", 50.0)))
            return {"depth": self._dent_depth, "angle_deg": math.degrees(self._dent_angle)}
        if kind == "reset":
            self._dent_depth = 0.0
            self._bursts.clear()
            self._drift_t0 = None
            self._drift_rate_per_s = 0.0
            self._drift_offset = 0.0
            self._noise_until = 0.0
            self._dropout_until = 0.0
            return {}
        raise SourceError(f"unknown demo event: {kind}")

    def _stop_drift(self, now: float) -> None:
        if self._drift_t0 is not None:
            self._drift_offset += self._drift_rate_per_s * max(0.0, now - self._drift_t0)
        self._drift_t0 = None
        self._drift_rate_per_s = 0.0
