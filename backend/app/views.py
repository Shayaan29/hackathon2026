"""Data behind the cockpit's live signal chart: one sensor over a time window, peaks preserved."""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from .analysis import decimate_minmax, to_json_list
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
            "display": cfg.display.as_dict() if cfg.display else None,
            "limit_word": cfg.limit_word,
        })
    events = [e for e in engine.recent_events(limit=100, since=now - window_s)
              if e["sensor"] in sensor_ids or set(e["related_sensors"]) & set(sensor_ids)]
    return {
        "view": "timeseries",
        "window_s": window_s,
        "t_now": now,
        "series": series,
        "events": events,
    }
