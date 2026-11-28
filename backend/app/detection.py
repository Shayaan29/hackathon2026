"""Alert rules on the signal itself. The part check (partcheck.py) watches what lies under the sensor.

* out_of_range  the target leaves the measuring range of the sensor (or the tolerance band of a part)
* invalid       too many invalid readings: no target, out of range, glare

Each rule fires once when its condition starts, then stays quiet until the condition has cleared for
``rearm_s`` and the cooldown has passed. Both rules work from the first reading on.

The detector also learns the sensor's normal noise, which the sensor tile shows.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from .analysis import robust_sigma
from .buffer import SensorBuffer
from .config import DetectionConfig, SensorConfig


@dataclass
class Event:
    id: str
    sensor: str
    kind: str
    severity: str
    t: float
    numbers: dict[str, Any]
    summary: str
    forecast_min: float | None = None
    related_sensors: list[str] = field(default_factory=list)

    def to_message(self) -> dict[str, Any]:
        return {"type": "event", **asdict(self)}


@dataclass
class _State:
    baseline_sigma: float | None = None
    last_fired: dict[str, float] = field(default_factory=dict)
    active: dict[str, bool] = field(default_factory=dict)
    clear_since: dict[str, float] = field(default_factory=dict)


@dataclass
class Metrics:
    """What the rules measured for one sensor in one evaluation."""

    mean_short: float | None = None
    invalid_share: float = 0.0


class Detector:
    def __init__(self, sensors: tuple[SensorConfig, ...], cfg: DetectionConfig, rate_hz: int) -> None:
        self.sensors = {s.id: s for s in sensors}
        self.cfg = cfg
        self.rate_hz = rate_hz
        self.state = {s.id: _State() for s in sensors}
        self.last_metrics: dict[str, Metrics] = {}
        self._ids = itertools.count(1)

    # -- public ------------------------------------------------------------

    def reset(self) -> None:
        self.state = {sid: _State() for sid in self.sensors}
        self.last_metrics = {}

    def baseline(self, sensor_id: str) -> float | None:
        return self.state[sensor_id].baseline_sigma

    def active_kinds(self, sensor_id: str) -> list[str]:
        return [kind for kind, on in self.state[sensor_id].active.items() if on]

    def evaluate(self, buffers: dict[str, SensorBuffer], now: float) -> list[Event]:
        metrics: dict[str, Metrics] = {}
        for sid, buf in buffers.items():
            m = self._measure(sid, buf)
            if m is not None:
                metrics[sid] = m
        self.last_metrics = metrics
        events: list[Event] = []
        for sid, m in metrics.items():
            for event in (self._out_of_range(sid, m, now), self._invalid(sid, m, now)):
                if event is not None:
                    events.append(event)
        return events

    # -- measurement -------------------------------------------------------

    def _measure(self, sid: str, buf: SensorBuffer) -> Metrics | None:
        cfg = self.cfg
        if buf.t_last is None:
            return None
        self._learn_noise(self.state[sid], buf)
        _, _, invalid_win = buf.fast_window(cfg.invalid_window_s)
        _, _, oor_win = buf.fast_window(cfg.out_of_range_window_s)
        m = Metrics()
        m.invalid_share = float(np.mean(~np.isfinite(invalid_win))) if invalid_win.size else 0.0
        ok = oor_win[np.isfinite(oor_win)]
        m.mean_short = float(ok.mean()) if ok.size else None
        return m

    def _learn_noise(self, st: _State, buf: SensorBuffer) -> None:
        """The sensor's normal noise: learned from the first ``baseline_s`` seconds, then it follows slowly while
        the signal is calm. It ignores steps, so putting a part down does not count as noise."""
        if st.baseline_sigma is None:
            if buf.fast_seconds_available < self.cfg.baseline_s:
                return
            _, _, base = buf.fast_window(self.cfg.baseline_s)
            st.baseline_sigma = robust_sigma(base) or None
            return
        _, _, short = buf.fast_window(1.0)
        recent = robust_sigma(short)
        if recent and recent < 1.5 * st.baseline_sigma and bool(np.all(np.isfinite(short))):
            st.baseline_sigma = 0.99 * st.baseline_sigma + 0.01 * recent

    # -- firing logic ------------------------------------------------------

    def _gate(self, sid: str, kind: str, condition: bool, now: float) -> bool:
        st = self.state[sid]
        if condition:
            st.clear_since.pop(kind, None)
            if st.active.get(kind):
                return False
            if now - st.last_fired.get(kind, -math.inf) < self.cfg.cooldown_s.get(kind, 30.0):
                return False
            st.active[kind] = True
            st.last_fired[kind] = now
            return True
        if st.active.get(kind):
            since = st.clear_since.setdefault(kind, now)
            if now - since >= self.cfg.rearm_s:
                st.active[kind] = False
                st.clear_since.pop(kind, None)
        return False

    def _new_id(self, now: float) -> str:
        return f"ev-{int(now) % 100000}-{next(self._ids)}"

    def _name(self, sid: str) -> str:
        return self.sensors[sid].name

    # -- rules -------------------------------------------------------------

    def _out_of_range(self, sid: str, m: Metrics, now: float) -> Event | None:
        sensor = self.sensors[sid]
        value = m.mean_short
        outside = value is not None and not sensor.tolerance.contains(value)
        if not self._gate(sid, "out_of_range", outside, now) or value is None:
            return None
        limit = sensor.tolerance.high if value > sensor.tolerance.high else sensor.tolerance.low
        numbers: dict[str, Any] = {"value": round(value, 1), "limit": limit, "unit": sensor.unit}
        if sensor.tolerance_kind == "measuring_range" and sensor.spec is not None:
            summary = self._range_summary(sensor, value, limit, numbers)
        else:
            summary = (f"{sensor.name} is out of tolerance: {sensor.pos(value)} "
                       f"against a limit of {sensor.pos(limit)}.")
        return Event(
            id=self._new_id(now), sensor=sid, kind="out_of_range", severity="critical", t=now,
            numbers=numbers, summary=summary,
        )

    @staticmethod
    def _range_summary(sensor: SensorConfig, value: float, limit: float, numbers: dict[str, Any]) -> str:
        """The target left the measuring range of the sensor: say where it is, in the sensor's own terms."""
        spec = sensor.spec
        assert spec is not None
        zone = spec.zone(value)
        numbers.update({"zone": zone.name, "led": zone.led, "range": spec.range_text,
                        "extended_range": spec.extended_text})
        away = sensor.pos(abs(value - limit))
        where = "before the start of" if value < limit else "beyond the end of"
        text = (f"{sensor.name} is outside its measuring range: the target is at {sensor.pos(value)}, "
                f"{away} {where} the {spec.range_text} range.")
        if zone.name in ("before", "after"):
            text += (f" The sensor still measures in its extended range ({spec.extended_text}), "
                     f"but the data sheet accuracy only holds inside {spec.range_text}.")
        else:
            text += f" That is outside even the extended range of {spec.extended_text}."
        return text

    def _invalid(self, sid: str, m: Metrics, now: float) -> Event | None:
        bad = m.invalid_share >= self.cfg.invalid_min_share
        if not self._gate(sid, "invalid", bad, now):
            return None
        share = round(m.invalid_share * 100)
        return Event(
            id=self._new_id(now), sensor=sid, kind="invalid", severity="warning", t=now,
            numbers={"invalid_percent": share},
            summary=(f"{self._name(sid)} reports invalid readings: {share}% of the last second. "
                     "The target may be out of range, tilted, or too shiny or dark."),
        )
