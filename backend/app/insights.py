"""Explanations for the chart views: what a person should notice, step by step.

Every step is one sentence plus an anchor that tells the cockpit what to point
at while the sentence is spoken. All numbers come from the data; the voice model
only rephrases them. Anchor types (x values in the chart's own units, ``t`` in
Unix seconds for time axes):

  x        vertical line            {"t"|"x", "label", "sensor"?}
  point    a pulsing dot            {"t"|"x", "y", "label", "sensor"?}
  xspan    shaded x range           {"t0","t1"|"x0","x1", "label"}
  yspan    shaded y band            {"y0","y1", "label", "sensor"?}
  grid     a whole sub-chart        {"sensor", "label"}
  category one box or bar           {"index", "label"}
  line     a straight line          {"x0","y0","x1","y1", "label"}
  zoom     fly the timeline here    {"t0","t1", "label"?}
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import numpy as np

from .engine import Engine


def clock(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%H:%M:%S")


def fmt(v: float, digits: int = 1) -> str:
    return f"{v:.{digits}f}".replace("-0.0", "0.0")


def signed(v: float, digits: int = 1) -> str:
    return f"{v:+.{digits}f}"


def step(text: str, anchor: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"text": text, "anchor": anchor}


def window_words(seconds: float) -> str:
    if seconds >= 5400:
        return f"the last {seconds / 3600:.0f} hours"
    if seconds >= 120:
        return f"the last {seconds / 60:.0f} minutes"
    return f"the last {seconds:.0f} seconds"


def measuring(cfg: Any) -> bool:
    """True when the limits of this sensor are its measuring range (data sheet), not a part's tolerance."""
    return cfg.tolerance_kind == "measuring_range" and cfg.spec is not None


def band_words(cfg: Any) -> str:
    """The limits as they are named in a sentence."""
    if measuring(cfg):
        return f"the measuring range of {cfg.spec.range_text}"
    return f"the tolerance of {cfg.pos(cfg.tolerance.low, 0)} to {cfg.pos(cfg.tolerance.high, 0)}"


def samples(engine: Engine, sid: str, window_s: float) -> tuple[np.ndarray, np.ndarray, str]:
    """(times, finite values, what one value is) for a window, finest data available."""
    buf = engine.buffers[sid]
    if window_s <= buf.fast_seconds_available + 1.0:
        t0, dt, v = buf.fast_window(window_s)
        t = t0 + np.arange(v.size) * dt
        per = "readings"
    else:
        slow = buf.slow_window(window_s)
        t, v = slow["t"], slow["mean"]
        per = "one-second averages"
    ok = np.isfinite(v)
    return t[ok], v[ok], per


# -- timeseries and compare ---------------------------------------------------

def _history_single(engine: Engine, data: dict[str, Any]) -> list[dict[str, Any]]:
    s = data["series"][0]
    sid = s["sensor"]
    cfg = engine.settings.sensor(sid)
    t, v, _ = samples(engine, sid, data["window_s"])
    if v.size < 10:
        return []
    steps = []
    p5, p95 = np.percentile(v, [5, 95])
    steps.append(step(f"Over {window_words(data['window_s'])}, {cfg.name} mostly stayed between "
                      f"{cfg.pos(float(p5))} and {cfg.pos(float(p95))}.",
                      {"type": "yspan", "y0": float(p5), "y1": float(p95), "sensor": sid, "label": "normal range"}))
    moments = data["events"][-2:]
    for e in moments:
        steps.append(step(f"At {clock(e['t'])}: {e['summary']}",
                          {"type": "x", "t": e["t"], "sensor": sid, "label": e["kind"].replace("_", " ")}))
    dev = np.abs(v - cfg.nominal)
    i = int(dev.argmax())
    if not any(abs(e["t"] - t[i]) < 3.0 for e in moments):
        what = "The reading furthest from the middle of the range" if measuring(cfg) else "The largest deviation"
        steps.append(step(f"{what} was {cfg.pos(float(v[i]))}, at {clock(float(t[i]))}.",
                          {"type": "point", "t": float(t[i]), "y": float(v[i]), "sensor": sid, "label": "largest"}))
    last = float(v[-1])
    if measuring(cfg):
        text = f"Right now it reads {cfg.pos(last)}, {cfg.spec.zone(last).text}."
    else:
        inside = cfg.tolerance.low <= last <= cfg.tolerance.high
        text = (f"Right now it reads {cfg.pos(last)}, {'well inside' if inside else 'outside'} "
                f"{band_words(cfg)}.")
    steps.append(step(text, {"type": "point", "t": float(t[-1]), "y": last, "sensor": sid, "label": "now"}))
    return steps


def for_history(engine: Engine, data: dict[str, Any]) -> list[dict[str, Any]]:
    if not data.get("series"):
        return []
    return _history_single(engine, data)
