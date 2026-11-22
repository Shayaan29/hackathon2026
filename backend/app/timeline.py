"""The shift timeline: one sensor from the start of the shift down to single readings.

Three kinds of data back the zoom, and ``range_data`` picks the finest one that
covers the requested window:

* full rate   the last ``fast_buffer_s`` seconds, every single reading
* snapshot    full-rate signal saved around every alert, kept for the whole shift
* seconds     min, mean and max of every second, for the whole shift

Thinning turns each slice of time into its average (the line) and its lowest
and highest reading (the band around it), so a spike of a few milliseconds
stays visible in the eight-hour view without the line turning into a zigzag.
"""

from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Any

import numpy as np

from .analysis import to_json_list
from .engine import Engine

KIND_WORD = {"vibration": "vibration", "noise": "noise", "drift": "drift",
             "out_of_range": "out of tolerance", "invalid": "invalid readings"}

# how far around an incident the tour zooms (seconds before, after)
ZOOM_AROUND = {"vibration": (0.4, 1.6), "drift": (600.0, 900.0), "noise": (120.0, 600.0),
               "invalid": (20.0, 45.0), "out_of_range": (3.0, 9.0)}


def clock(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%H:%M:%S")


def _slices(values: np.ndarray, bucket: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    """Average, lowest and highest of every ``bucket`` values; invalid readings are skipped,
    a slice with none left is a gap. The oldest few values that do not fill a slice are dropped."""
    m = values.size // bucket
    skip = values.size - m * bucket
    blocks = values[skip:].reshape(m, bucket)
    ok = np.isfinite(blocks)
    count = ok.sum(axis=1)
    has = count > 0
    lo = np.where(has, np.where(ok, blocks, np.inf).min(axis=1), np.nan)
    hi = np.where(has, np.where(ok, blocks, -np.inf).max(axis=1), np.nan)
    mean = np.where(has, np.where(ok, blocks, 0.0).sum(axis=1) / np.maximum(count, 1), np.nan)
    return mean, lo, hi, skip


def envelope(values: np.ndarray, start: float, dt: float, points: int) -> dict[str, Any]:
    """At most ``points`` slices of an evenly sampled signal, each with its time."""
    n = values.size
    bucket = max(1, math.ceil(n / max(points, 1)))
    if bucket == 1:
        return {"t": start + np.arange(n) * dt, "v": values, "lo": values, "hi": values, "resolution": dt}
    mean, lo, hi, skip = _slices(values, bucket)
    t = start + (skip + np.arange(mean.size) * bucket + (bucket - 1) / 2) * dt
    return {"t": t, "v": mean, "lo": lo, "hi": hi, "resolution": bucket * dt}


def envelope_seconds(t: np.ndarray, mean: np.ndarray, lo: np.ndarray, hi: np.ndarray,
                     points: int) -> dict[str, Any]:
    """The same for per-second summaries: lowest of the lows, highest of the highs."""
    bucket = max(1, math.ceil(t.size / max(points, 1)))
    if bucket == 1:
        return {"t": t, "v": mean, "lo": lo, "hi": hi, "resolution": 1.0}
    v, _, _, skip = _slices(mean, bucket)
    _, low, _, _ = _slices(lo, bucket)
    _, _, high, _ = _slices(hi, bucket)
    times = t[skip:].reshape(-1, bucket).mean(axis=1)
    return {"t": times, "v": v, "lo": low, "hi": high, "resolution": float(bucket)}


def _snapshot_for(engine: Engine, sid: str, t0: float, t1: float) -> tuple[float, float, np.ndarray] | None:
    best, best_cover = None, 0.0
    for s0, dt, values in engine.snapshots.get(sid, []):
        s1 = s0 + values.size * dt
        cover = max(0.0, min(t1, s1) - max(t0, s0)) / max(t1 - t0, 1e-9)
        if cover > best_cover:
            best, best_cover = (s0, dt, values), cover
    return best if best_cover >= 0.6 else None


def range_data(engine: Engine, sid: str, t0: float, t1: float, points: int = 1600) -> dict[str, Any]:
    buf = engine.buffers[sid]
    cfg = engine.settings.sensor(sid)
    t_last = buf.t_last or time.time()
    oldest = t_last - engine.settings.engine.slow_buffer_s
    t0, t1 = max(float(t0), oldest), min(float(t1), t_last)
    if t1 - t0 < 0.01:
        t0 = t1 - 0.01
    fast_start = t_last - buf.fast_seconds_available
    source = "seconds"
    if t0 >= fast_start:
        start, dt, values = buf.fast_range(t0, t1)
        env = envelope(values, start, dt, points)
        source = "full"
    else:
        snap = _snapshot_for(engine, sid, t0, t1) if t1 - t0 <= 20 else None
        if snap is not None:
            s0, dt, values = snap
            i0 = max(0, int((t0 - s0) / dt))
            i1 = min(values.size, int((t1 - s0) / dt) + 2)
            env = envelope(values[i0:i1], s0 + i0 * dt, dt, points)
            source = "snapshot"
        else:
            slow = buf.slow_window(t_last - t0 + 2.0)
            keep = (slow["t"] >= t0 - 1.0) & (slow["t"] <= t1 + 1.0)
            env = envelope_seconds(slow["t"][keep], slow["mean"][keep], slow["min"][keep], slow["max"][keep], points)
    events = [e for e in engine.all_events(since=t0 - 1.0, sensor=sid) if e["t"] <= t1 + 1.0]
    return {
        "view": "shift", "sensor": sid, "name": cfg.name, "unit": cfg.unit,
        "tolerance": cfg.tolerance.as_dict(), "nominal": cfg.nominal,
        "t0": round(t0, 3), "t1": round(t1, 3), "t_now": round(t_last, 3),
        "oldest": round(oldest, 1), "fast_start": round(fast_start, 3),
        "t": [round(float(x), 4) for x in env["t"]], "v": to_json_list(env["v"], 3),
        "lo": to_json_list(env["lo"], 3), "hi": to_json_list(env["hi"], 3),
        "source": source, "resolution_s": round(float(env["resolution"]), 6),
        "events": events,
    }


def _in_tolerance_share(engine: Engine, sid: str, since: float) -> float | None:
    """Share of seconds in which every reading of the sensor was inside its tolerance."""
    cfg = engine.settings.sensor(sid)
    slow = engine.buffers[sid].slow_window(time.time() - since)
    lo, hi = slow["min"], slow["max"]
    ok = np.isfinite(lo) & np.isfinite(hi)
    if ok.sum() == 0:
        return None
    return float(((lo[ok] >= cfg.tolerance.low) & (hi[ok] <= cfg.tolerance.high)).mean())


def shift(engine: Engine, sid: str, points: int = 1800) -> dict[str, Any]:
    """The whole shift for one sensor, plus a guided tour through its incidents."""
    now = engine.buffers[sid].t_last or time.time()
    start = now - engine.settings.engine.slow_buffer_s
    data = range_data(engine, sid, start, now, points)
    name = engine.settings.sensor(sid).name
    line_events = engine.all_events(since=start)
    events = engine.all_events(since=start, sensor=sid)  # this sensor's own, and knocks it shared
    hours = (now - max(start, data["oldest"])) / 3600.0
    share = _in_tolerance_share(engine, sid, start)

    steps: list[dict[str, Any]] = []
    if len(events) == len(line_events):
        intro = f"Over the last {hours:.0f} hours there were {len(events)} alerts on this line, all involving {name}."
    else:
        intro = (f"Over the last {hours:.0f} hours there were {len(line_events)} alerts on this line; "
                 f"{len(events)} of them involved {name}.")
    if share is not None:
        if share >= 1.0:
            intro += f" {name} stayed in tolerance the whole time."
        else:
            pct = "more than 99.9" if share > 0.999 else f"{share * 100:.1f}"
            intro += f" {name} stayed in tolerance {pct}% of the time."
    steps.append({"text": intro, "anchor": {"type": "zoom", "t0": start, "t1": now}})

    # pick a few incidents worth showing: the worst drift, one knock, the signal loss
    picked: list[dict[str, Any]] = []
    for kind, severity in (("drift", "critical"), ("vibration", None), ("invalid", None), ("noise", None)):
        match = [e for e in events if e["kind"] == kind and (severity is None or e["severity"] == severity)]
        if match and len(picked) < 3:
            picked.append(match[0] if kind == "vibration" else match[-1])
    for e in sorted(picked, key=lambda x: x["t"]):
        before, after = ZOOM_AROUND.get(e["kind"], (30.0, 60.0))
        text = f"At {clock(e['t'])}: {e['summary']}"
        if e["kind"] == "vibration":
            text += " Zoomed in this far, every single reading is visible: the fixture ringing."
        steps.append({"text": text, "anchor": {"type": "zoom", "t0": e["t"] - before, "t1": e["t"] + after,
                                               "label": KIND_WORD.get(e["kind"], e["kind"])}})
    unsettled = [engine.settings.sensor(s).name for s in engine.buffers if engine.sensor_state(s) != "ok"]
    closing = (f"These are the last ten minutes. Right now {', '.join(unsettled)} needs attention."
               if unsettled else "These are the last ten minutes: right now everything is in tolerance.")
    steps.append({"text": closing, "anchor": {"type": "zoom", "t0": now - 600, "t1": now}})
    data["insights"] = steps
    data["shift_start"] = round(start, 1)
    return data
