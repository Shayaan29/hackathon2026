"""Alert rules: turn the raw signal into a few meaningful events.

Every rule compares the signal with what is normal for that sensor. "Normal"
noise is learned during the first seconds (``baseline_s``) and then follows
slowly while nothing is wrong.

Rules (all thresholds live in sensors.yaml under ``detection``):

* vibration     short-window spread well above normal AND one clear frequency
* noise         spread above normal for a while, without one clear frequency
* drift         a straight, steady trend in the per-second averages
* out_of_range  the value leaves the tolerance band
* invalid       too many invalid readings (no target, out of range, glare)

Each rule fires once when its condition starts, then stays quiet until the
condition has cleared for ``rearm_s`` and the cooldown has passed. A drift
warning fires again only when it escalates to critical.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from .analysis import best_trend, detrended_std, minutes_to_limit, robust_sigma, spectral_peak
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
    relearn_after: float | None = None
    relearn_s: float | None = None
    noise_since: float | None = None
    drift_since: float | None = None
    drift_severity: str | None = None
    last_fired: dict[str, float] = field(default_factory=dict)
    active: dict[str, bool] = field(default_factory=dict)
    clear_since: dict[str, float] = field(default_factory=dict)


@dataclass
class Metrics:
    """What the rules measured for one sensor in one evaluation."""

    ratio_short: float = 0.0
    ratio_noise: float = 0.0
    peak: dict[str, float] | None = None
    peak_medium: dict[str, float] | None = None
    peak_dev: float = 0.0
    mean_short: float | None = None
    invalid_share: float = 0.0
    drift: dict[str, float] | None = None


def _fmt(value: float, digits: int = 1) -> str:
    """Signed number for speech-friendly summaries: +6.0, -12.5, 0.0."""
    if abs(value) < 0.5 * 10 ** -digits:
        return f"{0:.{digits}f}"
    return f"{value:+.{digits}f}"


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

    def relearn(self, sensor_id: str, now: float, seconds: float = 5.0) -> None:
        """Forget what normal noise looks like and learn it again from fresh data,
        for example after the operator changed averaging on purpose."""
        st = self.state[sensor_id]
        st.baseline_sigma = None
        st.relearn_after = now + seconds
        st.relearn_s = seconds
        st.noise_since = None

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
        events.extend(self._vibration(metrics, now))
        for sid, m in metrics.items():
            for event in (self._noise(sid, m, now), self._drift(sid, m, now),
                          self._out_of_range(sid, m, now), self._invalid(sid, m, now)):
                if event is not None:
                    events.append(event)
        return events

    # -- measurement -------------------------------------------------------

    def _measure(self, sid: str, buf: SensorBuffer) -> Metrics | None:
        cfg, st = self.cfg, self.state[sid]
        if buf.t_last is None:
            return None
        if st.baseline_sigma is None:
            if st.relearn_after is not None:
                if buf.t_last < st.relearn_after:
                    return None
                _, _, base = buf.fast_window(st.relearn_s or cfg.baseline_s)
                st.relearn_after = None
            else:
                if buf.fast_seconds_available < cfg.baseline_s:
                    return None
                _, _, base = buf.fast_window(cfg.baseline_s)
            st.baseline_sigma = robust_sigma(base)
            if not st.baseline_sigma:
                return None

        _, _, short = buf.fast_window(cfg.vibration_window_s)
        _, _, medium = buf.fast_window(cfg.noise_window_s)
        _, _, invalid_win = buf.fast_window(cfg.invalid_window_s)
        _, _, oor_win = buf.fast_window(cfg.out_of_range_window_s)

        m = Metrics()
        base = st.baseline_sigma
        std_short = detrended_std(short)
        std_medium = detrended_std(medium)
        m.ratio_short = (std_short / base) if std_short is not None else 0.0
        m.ratio_noise = (std_medium / base) if std_medium is not None else 0.0
        m.invalid_share = float(np.mean(~np.isfinite(invalid_win))) if invalid_win.size else 0.0
        ok = oor_win[np.isfinite(oor_win)]
        m.mean_short = float(ok.mean()) if ok.size else None

        if m.ratio_short >= cfg.vibration_ratio * 0.5 or m.ratio_noise >= cfg.noise_ratio:
            m.peak = spectral_peak(short, self.rate_hz, fmin=cfg.vibration_min_hz)
            finite_short = short[np.isfinite(short)]
            if finite_short.size:
                m.peak_dev = float(np.max(np.abs(finite_short - np.median(finite_short))))
        if m.ratio_noise >= cfg.noise_ratio:
            m.peak_medium = spectral_peak(medium, self.rate_hz, fmin=cfg.vibration_min_hz)

        m.drift = self._drift_trend(buf)

        calm = m.ratio_short < 1.5 and m.ratio_noise < 1.5 and m.invalid_share == 0.0
        if calm:
            recent = robust_sigma(short)
            if recent:
                st.baseline_sigma = 0.99 * base + 0.01 * recent
        return m

    def _drift_trend(self, buf: SensorBuffer) -> dict[str, float] | None:
        cfg = self.cfg
        slow = buf.slow_window(cfg.drift_window_s)
        return best_trend(slow["t"], slow["mean"], (cfg.drift_window_s / 2.0, cfg.drift_window_s),
                          cfg.drift_min_r2, cfg.drift_min_rate_per_min)

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

    def _vibration(self, metrics: dict[str, Metrics], now: float) -> list[Event]:
        cfg = self.cfg

        def is_vibration(m: Metrics, scale: float = 1.0) -> bool:
            return (m.ratio_short >= cfg.vibration_ratio * scale and m.peak is not None
                    and m.peak["prominence"] >= cfg.vibration_min_prominence * scale)

        firing = [sid for sid, m in metrics.items()
                  if self._gate(sid, "vibration", is_vibration(m), now)]
        if not firing:
            return []
        primary = max(firing, key=lambda sid: metrics[sid].peak_dev)
        m = metrics[primary]
        fixture = self.sensors[primary].fixture
        related = [sid for sid, other in metrics.items()
                   if sid != primary and self.sensors[sid].fixture == fixture
                   and (sid in firing or is_vibration(other, 0.5))]
        for sid in related:
            st = self.state[sid]
            st.active["vibration"] = True
            st.last_fired["vibration"] = now
        unit = self.sensors[primary].unit
        freq = m.peak["frequency_hz"] if m.peak else 0.0
        numbers = {
            "frequency_hz": round(freq, 1),
            "peak": round(m.peak_dev, 1),
            "times_normal_noise": round(m.ratio_short, 1),
            "unit": unit,
        }
        summary = (f"{self._name(primary)} picked up a {freq:.0f} Hz vibration, "
                   f"{m.peak_dev:.1f} {unit} peak.")
        if related:
            names = " and ".join(self._name(s) for s in related)
            verb = "saw" if len(related) == 1 else "all saw"
            summary += f" {names} {verb} it at the same moment."
        return [Event(id=self._new_id(now), sensor=primary, kind="vibration", severity="warning",
                      t=now, numbers=numbers, summary=summary, related_sensors=related)]

    def _noise(self, sid: str, m: Metrics, now: float) -> Event | None:
        cfg, st = self.cfg, self.state[sid]
        clean_peak = any(p is not None and p["prominence"] >= cfg.vibration_min_prominence
                         for p in (m.peak, m.peak_medium))
        noisy = (m.ratio_noise >= cfg.noise_ratio and m.ratio_short >= cfg.noise_ratio
                 and not clean_peak and not st.active.get("vibration"))
        if noisy:
            st.noise_since = st.noise_since or now
        else:
            st.noise_since = None
        sustained = noisy and (now - (st.noise_since or now)) >= cfg.noise_hold_s
        if not self._gate(sid, "noise", sustained, now):
            return None
        unit = self.sensors[sid].unit
        sigma = (st.baseline_sigma or 0.0) * m.ratio_noise
        return Event(
            id=self._new_id(now), sensor=sid, kind="noise", severity="warning", t=now,
            numbers={"times_normal_noise": round(m.ratio_noise, 1), "noise": round(sigma, 2), "unit": unit},
            summary=(f"{self._name(sid)} is {m.ratio_noise:.1f} times noisier than normal "
                     f"({sigma:.2f} {unit} instead of {st.baseline_sigma or 0:.2f})."),
        )

    def _drift(self, sid: str, m: Metrics, now: float) -> Event | None:
        cfg, st = self.cfg, self.state[sid]
        sensor = self.sensors[sid]
        drift = m.drift
        minutes = None
        severity = "warning"
        if drift is not None:
            minutes = minutes_to_limit(drift["current"], drift["rate_per_min"],
                                       sensor.tolerance.low, sensor.tolerance.high)
            if minutes is not None and minutes <= cfg.drift_critical_minutes:
                severity = "critical"
        if drift is not None:
            st.drift_since = st.drift_since or now
        else:
            st.drift_since = None
        steady = drift is not None and (now - (st.drift_since or now)) >= cfg.drift_hold_s
        escalated = (steady and st.active.get("drift")
                     and severity == "critical" and st.drift_severity != "critical")
        fire = self._gate(sid, "drift", steady, now)
        if escalated:
            st.last_fired["drift"] = now
            fire = True
        if not fire or drift is None:
            if not st.active.get("drift"):
                st.drift_severity = None
            return None
        st.drift_severity = severity
        rate = drift["rate_per_min"]
        unit = sensor.unit
        summary = f"{sensor.name} is drifting {_fmt(rate)} {unit} per minute"
        if minutes is not None:
            summary += (f"; at this rate it leaves tolerance in about {minutes:.0f} minutes."
                        if minutes >= 1 else "; it is about to leave tolerance.")
        else:
            summary += ", moving back towards the middle of the tolerance band."
        return Event(
            id=self._new_id(now), sensor=sid, kind="drift", severity=severity, t=now,
            numbers={"rate_per_min": round(rate, 2), "current": round(drift["current"], 1),
                     "r2": round(drift["r2"], 2), "unit": unit,
                     "tolerance_low": sensor.tolerance.low, "tolerance_high": sensor.tolerance.high},
            summary=summary,
            forecast_min=None if minutes is None else round(minutes, 1),
        )

    def _out_of_range(self, sid: str, m: Metrics, now: float) -> Event | None:
        sensor = self.sensors[sid]
        value = m.mean_short
        outside = value is not None and not sensor.tolerance.contains(value)
        if not self._gate(sid, "out_of_range", outside, now) or value is None:
            return None
        limit = sensor.tolerance.high if value > sensor.tolerance.high else sensor.tolerance.low
        return Event(
            id=self._new_id(now), sensor=sid, kind="out_of_range", severity="critical", t=now,
            numbers={"value": round(value, 1), "limit": limit, "unit": sensor.unit},
            summary=(f"{sensor.name} is out of tolerance: {_fmt(value)} {sensor.unit} "
                     f"against a limit of {_fmt(limit)} {sensor.unit}."),
        )

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
