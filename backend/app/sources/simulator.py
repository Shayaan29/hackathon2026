"""Realistic dummy data for one displacement sensor.

The signal is built from the parts a real measurement shows:

* white measurement noise (shrinks when averaging goes up)
* a slow, bounded wander (temperature, air movement)
* a small machine hum at a fixed frequency
* demo events you trigger: a table tap (damped vibration), a steady drift,
  a noise burst and a dropout with invalid readings
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..config import SensorConfig
from .base import SensorSource, SourceError


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
        averaging = cfg.settings.get("averaging")
        self._averaging_reference = float(averaging.default) if averaging else None
        self._averaging = self._averaging_reference
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

    def apply_setting(self, name: str, value: Any) -> None:
        if name == "averaging":
            self._averaging = float(value)

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


# -- an earlier stretch of the shift ------------------------------------------

def make_history(sources: list[SimulatedSensor], fixtures: dict[str, str], start: float, end: float,
                 seed: int = 7) -> dict[str, Any]:
    """Per-second min/mean/max for the earlier part of the shift, with a few incidents.

    Simulator only: the cockpit opens with a whole shift to explore. Knocks also
    get full-rate snapshots, like the ones the engine saves around live alerts.
    Returns {"seconds": {id: (t, mean, lo, hi)}, "events": [...], "snapshots": {id: [...]}}.
    """
    rng = np.random.default_rng(seed)
    span = end - start
    t = np.arange(math.ceil(start) + 0.5, end, 1.0)
    n = t.size
    by_id = {s.cfg.id: s for s in sources}
    first = sources[0]
    last = sources[-1]
    fixture_mates = [s.cfg.id for s in sources if fixtures.get(s.cfg.id) == fixtures.get(first.cfg.id)]

    seconds: dict[str, list[np.ndarray]] = {}
    for s in sources:
        sigma = s.noise
        base = s.cfg.nominal + s.wander(t) + s._rotation(t)
        k = 3.05 + 0.18 * rng.standard_normal(n)
        spread = k * sigma + s.hum_amplitude + 0.5 * s.runout * s.part_omega
        lo = base - spread * (0.95 + 0.1 * rng.random(n))
        hi = base + spread * (0.95 + 0.1 * rng.random(n))
        seconds[s.cfg.id] = [base, lo, hi]

    events: list[dict[str, Any]] = []
    snapshots: dict[str, list[tuple[float, float, np.ndarray]]] = {s.cfg.id: [] for s in sources}
    unit = first.cfg.unit

    def at(fraction: float) -> float:
        return start + span * fraction + float(rng.uniform(-90, 90))

    def idx(when: float) -> int:
        return int(np.clip(np.searchsorted(t, when), 0, n - 1))

    # noisy stretch on the first sensor: someone set its averaging too low
    t_noise = at(0.24)
    i0, i1 = idx(t_noise), idx(t_noise + 720)
    base, lo, hi = seconds[first.cfg.id]
    extra = (hi[i0:i1] - base[i0:i1]) * 1.8
    lo[i0:i1] -= extra
    hi[i0:i1] += extra
    events.append(dict(sensor=first.cfg.id, kind="noise", severity="warning", t=t_noise + 2,
                       numbers={"times_normal_noise": 2.8},
                       summary=f"{first.cfg.name} became noisy: 2.8 times its normal noise."))

    # knocks on the fixture, felt by every sensor on it
    for fraction in (0.11, 0.33, 0.48, 0.64, 0.86):
        t_k = at(fraction)
        amp = float(rng.uniform(12, 21))
        freq = float(rng.uniform(29.2, 30.8))
        i = idx(t_k)
        for sid in fixture_mates:
            s = by_id[sid]
            gain = 1.0 if sid == first.cfg.id else 0.72
            b, l_, h = seconds[sid]
            h[i] = max(h[i], b[i] + amp * gain * 0.97)
            l_[i] = min(l_[i], b[i] - amp * gain * 0.92)
            # full-rate snapshot around the knock
            dt = 1.0 / s.rate_hz
            ts = np.arange(t_k - 2.0, t_k + 4.0, dt)
            rel = ts - t_k
            burst = np.where(rel >= 0, amp * gain * np.exp(-np.clip(rel, 0, None) / 0.35)
                             * np.sin(2 * math.pi * freq * np.clip(rel, 0, None)), 0.0)
            values = (s.cfg.nominal + s.wander(ts) + s._rotation(ts)
                      + s.noise * rng.standard_normal(ts.size)
                      + s.hum_amplitude * np.sin(2 * math.pi * s.hum_hz * ts) + burst)
            snapshots[sid].append((float(ts[0]), dt, values))
        others = [m for m in fixture_mates if m != first.cfg.id]
        also = f" {', '.join(by_id[m].cfg.name for m in others)} saw it at the same moment." if others else ""
        events.append(dict(sensor=first.cfg.id, kind="vibration", severity="warning", t=t_k + 0.15,
                           numbers={"frequency_hz": round(freq, 1), "peak": round(amp, 1),
                                    "times_normal_noise": round(amp / first.noise / 2.2, 1)},
                           summary=f"{first.cfg.name} picked up a {freq:.0f} Hz vibration, "
                                   f"{amp:.1f} {unit} peak.{also}",
                           related_sensors=others))

    # drift on the last sensor, then a recalibration
    t_d = at(0.55)
    i0, i1 = idx(t_d), idx(t_d + 1080)
    ramp = np.clip((t[i0:i1] - t_d) / 60.0, 0, None) * 1.9
    for arr in seconds[last.cfg.id]:
        arr[i0:i1] += ramp
    events.append(dict(sensor=last.cfg.id, kind="drift", severity="warning", t=t_d + 360,
                       numbers={"rate_per_min": 1.9, "unit": last.cfg.unit}, forecast_min=9.0,
                       summary=f"{last.cfg.name} is drifting: +1.9 {last.cfg.unit} per minute. "
                               f"At this rate it leaves the tolerance in about 9 minutes."))
    events.append(dict(sensor=last.cfg.id, kind="drift", severity="critical", t=t_d + 900,
                       numbers={"rate_per_min": 1.9, "unit": last.cfg.unit}, forecast_min=3.0,
                       summary=f"{last.cfg.name} keeps drifting: tolerance limit in about 3 minutes."))

    # a short loss of signal on the first sensor
    t_x = at(0.74)
    i0, i1 = idx(t_x), idx(t_x + 25)
    for arr in seconds[first.cfg.id]:
        arr[i0:i1] = np.nan
    events.append(dict(sensor=first.cfg.id, kind="invalid", severity="warning", t=t_x + 1,
                       numbers={"invalid_percent": 100},
                       summary=f"{first.cfg.name} reports invalid readings: 100% of the last second. "
                               "The target may be out of range, tilted, or too shiny or dark."))

    events.sort(key=lambda e: e["t"])
    return {"seconds": {sid: (t, *arrs) for sid, arrs in seconds.items()},
            "events": events, "snapshots": snapshots}
