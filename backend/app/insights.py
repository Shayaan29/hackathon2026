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

from .analysis import robust_sigma
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
    unit = cfg.unit
    t, v, _ = samples(engine, sid, data["window_s"])
    if v.size < 10:
        return []
    steps = []
    p5, p95 = np.percentile(v, [5, 95])
    steps.append(step(f"Over {window_words(data['window_s'])}, {cfg.name} mostly stayed between "
                      f"{signed(p5)} and {signed(p95)} {unit}.",
                      {"type": "yspan", "y0": float(p5), "y1": float(p95), "sensor": sid, "label": "normal range"}))
    moments = [e for e in data["events"] if e["kind"] != "drift"][-2:]
    for e in moments:
        steps.append(step(f"At {clock(e['t'])}: {e['summary']}",
                          {"type": "x", "t": e["t"], "sensor": sid, "label": e["kind"].replace("_", " ")}))
    dev = np.abs(v - cfg.nominal)
    i = int(dev.argmax())
    if not any(abs(e["t"] - t[i]) < 3.0 for e in moments):
        steps.append(step(f"The largest deviation was {signed(v[i])} {unit}, at {clock(float(t[i]))}.",
                          {"type": "point", "t": float(t[i]), "y": float(v[i]), "sensor": sid, "label": "largest"}))
    last = float(v[-1])
    inside = cfg.tolerance.low <= last <= cfg.tolerance.high
    steps.append(step(f"Right now it reads {signed(last)} {unit}, "
                      f"{'well inside' if inside else 'outside'} the tolerance of "
                      f"{signed(cfg.tolerance.low, 0)} to {signed(cfg.tolerance.high, 0)} {unit}.",
                      {"type": "point", "t": float(t[-1]), "y": last, "sensor": sid, "label": "now"}))
    return steps


def _history_compare(engine: Engine, data: dict[str, Any]) -> list[dict[str, Any]]:
    ids = [s["sensor"] for s in data["series"]]
    names = {sid: engine.settings.sensor(sid).name for sid in ids}
    unit = data["series"][0]["unit"]
    steps = []
    shared = [e for e in data["events"] if e.get("related_sensors")]
    for e in shared[-2:]:
        steps.append(step(f"At {clock(e['t'])} the sensors reacted at the same moment, so the cause was "
                          "shared: most likely the machine or the fixture, not one sensor.",
                          {"type": "x", "t": e["t"], "label": "together"}))
    if not shared:
        steps.append(step("Nothing shared hit these sensors in this window: each one only shows its own noise.",
                          None))
    noise = {}
    for sid in ids:
        _, v, _ = samples(engine, sid, min(data["window_s"], 30.0))
        sigma = robust_sigma(v) if v.size > 20 else None
        if sigma is not None:
            noise[sid] = sigma
    if len(noise) >= 2:
        loud = max(noise, key=noise.get)
        quiet = min(noise, key=noise.get)
        steps.append(step(f"{names[loud]} is the noisier one: {fmt(noise[loud], 2)} {unit} against "
                          f"{fmt(noise[quiet], 2)} {unit} for {names[quiet]}.",
                          {"type": "grid", "sensor": loud, "label": "noisier"}))
    return steps


def for_history(engine: Engine, data: dict[str, Any]) -> list[dict[str, Any]]:
    if not data.get("series"):
        return []
    return _history_compare(engine, data) if data["view"] == "compare" else _history_single(engine, data)


# -- spectrum -----------------------------------------------------------------

def for_spectrum(engine: Engine, data: dict[str, Any]) -> list[dict[str, Any]]:
    freqs = np.array(data["freqs"], dtype=float)
    amps = np.array([a if a is not None else np.nan for a in data["amps"]], dtype=float)
    fmin = engine.settings.detection.vibration_min_hz
    band = (freqs >= fmin) & np.isfinite(amps)
    if band.sum() < 5:
        return []
    unit = data["unit"]
    bg = float(np.median(amps[band]))
    peak = data.get("peak")
    steps = [step(f"Most of the spectrum is background noise, below about {fmt(bg * 3, 2)} {unit}.",
                  {"type": "yspan", "y0": 0.0, "y1": bg * 3, "label": "background"})]
    if peak and peak["prominence"] >= engine.settings.detection.vibration_min_prominence:
        f = peak["frequency_hz"]
        steps.append(step(f"One frequency stands out: {fmt(f, 0)} Hz with {fmt(peak['amplitude'], 2)} {unit}, "
                          f"{fmt(peak['prominence'], 0)} times the background. That is a real vibration.",
                          {"type": "point", "x": f, "y": peak["amplitude"], "label": f"{fmt(f, 0)} Hz"}))
        near = band & (np.abs(freqs - 2 * f) < 1.5)
        if near.any() and np.nanmax(amps[near]) > bg * 4:
            j = int(np.flatnonzero(near)[np.nanargmax(amps[near])])
            steps.append(step(f"The smaller line at {fmt(freqs[j], 0)} Hz is its echo, a harmonic of the same "
                              "vibration.", {"type": "point", "x": float(freqs[j]), "y": float(amps[j]),
                                             "label": "harmonic"}))
        if data.get("event"):
            steps.append(step(f"This spectrum is taken from the moment of the alert at "
                              f"{clock(data['event']['t'])}.", None))
    else:
        # a steady small line, for example the machine's own hum
        j = int(np.flatnonzero(band)[np.nanargmax(amps[band])])
        if amps[j] > bg * 4:
            steps.append(step(f"The small steady line at {fmt(freqs[j], 1)} Hz is always there: the "
                              "machine's normal hum, far too small to matter.",
                              {"type": "point", "x": float(freqs[j]), "y": float(amps[j]), "label": "hum"}))
        steps.append(step("No frequency stands out enough to be a vibration. The sensor sees a calm machine.",
                          None))
    return steps


# -- drift --------------------------------------------------------------------

def for_drift(engine: Engine, data: dict[str, Any]) -> list[dict[str, Any]]:
    t_rel = np.array(data["t_rel"], dtype=float)
    v = np.array([x if x is not None else np.nan for x in data["values"]], dtype=float)
    unit = data["unit"]
    if t_rel.size < 5:
        return []
    fit = data.get("fit")
    if not fit or abs(fit["rate_per_min"]) < 1.0:
        ok = np.isfinite(v)
        p5, p95 = np.percentile(v[ok], [5, 95]) if ok.any() else (0.0, 0.0)
        return [step(f"No steady drift: the one-second averages just wander between {signed(p5)} and "
                     f"{signed(p95)} {unit}.", {"type": "yspan", "y0": float(p5), "y1": float(p95), "label": "wander"})]
    start = float(fit["from_rel"])
    before = v[(t_rel < start) & np.isfinite(v)]
    steps = []
    if before.size > 3:
        steps.append(step(f"Until {window_words(-start).replace('the last ', '')} ago the value was steady "
                          f"around {signed(float(np.median(before)))} {unit}.",
                          {"type": "xspan", "x0": float(t_rel[0]), "x1": start, "label": "steady"}))
    steps.append(step(f"Since then it moves {signed(fit['rate_per_min'])} {unit} per minute, in a straight line.",
                      {"type": "xspan", "x0": start, "x1": 0.0, "label": f"{signed(fit['rate_per_min'])} {unit}/min"}))
    forecast = data.get("forecast") or {}
    crossing = forecast.get("crossing")
    if crossing and forecast.get("minutes_to_limit") is not None:
        steps.append(step(f"At this rate it reaches the tolerance limit of {signed(crossing['value'], 0)} {unit} "
                          f"in about {forecast['minutes_to_limit']:.0f} minutes. That is the time left to act.",
                          {"type": "point", "x": crossing["t_rel"], "y": crossing["value"], "label": "limit"}))
    return steps
