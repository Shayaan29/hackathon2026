"""Data behind the cockpit's chart views.

* timeseries  one sensor over a time window, peaks preserved
* compare     several sensors on one time axis
* spectrum    frequency content, centred on the last vibration when there is one
* drift       per-second averages, the fitted trend and the forecast to the limit
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from .analysis import (amplitude_spectrum, best_trend, decimate_minmax, minutes_to_limit,
                       round_or_none, spectral_peak, to_json_list)

TREND_WINDOWS = (15.0, 30.0, 60.0, 120.0, 300.0, 600.0)
from .engine import Engine


def history(engine: Engine, sensor_ids: list[str], window_s: float,
            max_points: int = 1200) -> dict[str, Any]:
    now = time.time()
    window_s = float(min(max(window_s, 2.0), engine.settings.engine.slow_buffer_s))
    series = []
    for sid in sensor_ids:
        buf = engine.buffers[sid]
        cfg = engine.settings.sensor(sid)
        t_end = buf.t_last or now
        if window_s <= buf.fast_seconds_available + 1.0:
            _, dt, values = buf.fast_window(window_s)
            out, bucket = decimate_minmax(values, max_points)
            dt_out = dt * bucket / 2.0 if bucket > 1 else dt
        else:
            slow = buf.slow_window(window_s)
            pairs = np.empty(slow["min"].size * 2)
            pairs[0::2], pairs[1::2] = slow["min"], slow["max"]
            out, bucket = decimate_minmax(pairs, max_points)
            dt_out = 0.5 * (bucket / 2.0 if bucket > 1 else 1.0)
        series.append({
            "sensor": sid,
            "name": cfg.name,
            "unit": cfg.unit,
            "t0": round(t_end - (out.size - 1) * dt_out, 4),
            "dt": dt_out,
            "values": to_json_list(out, 3),
            "tolerance": cfg.tolerance.as_dict(),
        })
    events = [e for e in engine.recent_events(limit=100, since=now - window_s)
              if e["sensor"] in sensor_ids or set(e["related_sensors"]) & set(sensor_ids)]
    return {
        "view": "timeseries" if len(sensor_ids) == 1 else "compare",
        "window_s": window_s,
        "t_now": now,
        "series": series,
        "events": events,
    }


def spectrum(engine: Engine, sensor_id: str, window_s: float = 2.0, event_id: str | None = None,
             max_hz: float = 100.0) -> dict[str, Any]:
    buf = engine.buffers[sensor_id]
    cfg = engine.settings.sensor(sensor_id)
    window_s = float(min(max(window_s, 0.5), 20.0))
    event = None
    if event_id:
        event = next((e for e in engine.events if e.id == event_id), None)
    if event is None:
        event = engine.last_event("vibration", sensor_id)
    t_last = buf.t_last or time.time()
    if event is not None and event.t >= t_last - buf.fast_seconds_available + window_s:
        t_start = event.t - 1.0
        t_stop = min(t_start + window_s, t_last)
        t_start = t_stop - window_s
        t0, _, values = buf.fast_range(t_start, t_stop)
    else:
        event = None
        t0, _, values = buf.fast_window(window_s)
    freqs, amps = amplitude_spectrum(values, engine.rate_hz)
    band = freqs <= max_hz
    peak = spectral_peak(values, engine.rate_hz, fmin=3.0, fmax=max_hz)
    return {
        "view": "spectrum",
        "sensor": sensor_id,
        "name": cfg.name,
        "unit": cfg.unit,
        "window_s": window_s,
        "t_start": round(t0, 3),
        "event": event.to_message() if event else None,
        "freqs": to_json_list(freqs[band], 2),
        "amps": to_json_list(amps[band], 4),
        "peak": None if peak is None else {
            "frequency_hz": round(peak["frequency_hz"], 1),
            "amplitude": round(peak["amplitude"], 3),
            "prominence": round(peak["prominence"], 1),
        },
    }


def drift(engine: Engine, sensor_id: str, window_s: float = 900.0, horizon_min: float = 60.0,
          max_points: int = 900) -> dict[str, Any]:
    buf = engine.buffers[sensor_id]
    cfg = engine.settings.sensor(sensor_id)
    slow = buf.slow_window(float(window_s))
    t, mean = slow["t"], slow["mean"]
    t_now = buf.t_last or time.time()
    result: dict[str, Any] = {
        "view": "drift", "sensor": sensor_id, "name": cfg.name, "unit": cfg.unit,
        "window_s": window_s, "tolerance": cfg.tolerance.as_dict(),
        "t_rel": [], "values": [], "fit": None, "forecast": None,
    }
    if t.size < 3:
        return result

    windows = tuple(w for w in TREND_WINDOWS if w <= window_s) or (float(window_s),)
    trend = best_trend(t, mean, windows, engine.settings.detection.drift_min_r2, t_end=t_now)

    values, bucket = decimate_minmax(mean, max_points)
    t_rel = np.linspace(float(t[0] - t_now), float(t[-1] - t_now), values.size)
    result["t_rel"] = to_json_list(t_rel, 1)
    result["values"] = to_json_list(values, 3)

    if trend is not None:
        slope = trend["slope_per_s"]
        current = trend["current"]
        rate = trend["rate_per_min"]
        minutes = minutes_to_limit(current, rate, cfg.tolerance.low, cfg.tolerance.high)
        horizon_s = float(horizon_min) * 60.0
        if minutes is not None:
            horizon_s = min(horizon_s, max(60.0, minutes * 60.0 * 1.25))
        crossing = None
        if minutes is not None and minutes * 60.0 <= horizon_s:
            limit = cfg.tolerance.high if rate > 0 else cfg.tolerance.low
            crossing = {"t_rel": round(minutes * 60.0, 1), "value": limit}
        result["fit"] = {"rate_per_min": round(rate, 2), "r2": round(trend["r2"], 3),
                         "from_rel": round(-trend["window_s"], 1)}
        result["forecast"] = {
            "points": [[0.0, round(current, 3)],
                       [round(horizon_s, 1), round(current + slope * horizon_s, 3)]],
            "minutes_to_limit": round_or_none(minutes, 1),
            "crossing": crossing,
            "horizon_s": round(horizon_s, 1),
        }
    return result
