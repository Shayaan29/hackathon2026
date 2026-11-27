"""Sensor setup assistant: reads the live signal and proposes better settings.

The logic follows how averaging behaves on a displacement sensor:

* Averaging N readings lowers random noise by about sqrt(N).
* It also slows the sensor down. A moving average over N readings at the
  measuring rate f has its first blind spot at f / N, so to still see a
  vibration of frequency v the sensor needs f / N >= 2 * v.

So the assistant raises averaging until the noise is low enough for the
tolerance band, and raises the measuring rate when that much averaging would
otherwise hide the vibrations the line cares about. It also shows a preview:
the last two seconds of real data, smoothed the way the new averaging would.
"""

from __future__ import annotations

import math
import time
from typing import Any

import numpy as np

from . import sensor_advice, sensor_specs
from .analysis import decimate_minmax, robust_sigma, round_or_none
from .engine import Engine, EngineError

# Vibration frequency to keep visible when no vibration has been seen yet.
DEFAULT_WATCH_HZ = 30.0
# Noise is a problem above this share of the tolerance band width...
NOISE_LIMIT_SHARE = 1.3 / 100.0
# ...and a proposal aims for this, so the fix leaves headroom.
NOISE_TARGET_SHARE = 1.0 / 160.0
PREVIEW_S = 2.0


def _pow2_at_least(value: float, lo: float, hi: float) -> int:
    n = 2 ** math.ceil(math.log2(max(value, 1.0)))
    return int(min(max(n, lo), hi))


def _watch_frequency(engine: Engine, sid: str, now: float) -> tuple[float, str]:
    """Highest vibration frequency seen on this sensor in the last 30 minutes."""
    freqs = [float(e["numbers"].get("frequency_hz", 0) or 0)
             for e in engine.recent_events(limit=200, since=now - 1800, sensor=sid)
             if e["kind"] == "vibration"]
    freqs = [f for f in freqs if f > 0]
    if freqs:
        return max(freqs), "the vibration seen on this sensor"
    return DEFAULT_WATCH_HZ, "the typical machine vibration"


def _moving_average(values: np.ndarray, m: int) -> np.ndarray:
    if m <= 1:
        return values.copy()
    filled = values.copy()
    bad = ~np.isfinite(filled)
    if bad.any():
        good = np.flatnonzero(~bad)
        if good.size == 0:
            return filled
        filled[bad] = np.interp(np.flatnonzero(bad), good, filled[good])
    kernel = np.ones(m) / m
    out = np.convolve(filled, kernel, mode="same")
    edge = m // 2
    out[:edge] = filled[:edge].mean() if edge else out[:edge]
    out[-edge:] = filled[-edge:].mean() if edge else out[-edge:]
    return out


def _datasheet_check(engine: Engine, sid: str, now: float) -> dict[str, Any]:
    """Setup check of a sensor with a data sheet whose settings live in its own web page.

    The cockpit cannot change those settings, so nothing is proposed. It compares what it measures with what the
    manufacturer states and says where to look in the sensor's web page."""
    cfg = engine.settings.sensor(sid)
    spec = cfg.spec
    assert spec is not None
    unit = cfg.unit
    _, _, recent = engine.buffers[sid].fast_window(3.0)
    noise = robust_sigma(recent)
    baseline = engine.detector.baseline(sid)
    invalid_share = float(np.mean(~np.isfinite(recent))) if recent.size else 0.0
    finite = recent[np.isfinite(recent)]
    linearity = spec.linearity_um
    reasons: list[str] = []
    found: list[str] = []          # what is wrong, in a few words each, for the summary line

    if invalid_share > 0.01:
        live = engine._live_status(sid) or {}
        invalid = live.get("invalid_reasons")
        main = sensor_specs.main_reason(invalid)
        why = f": {sensor_specs.reasons_text(invalid)}" if invalid else ""
        reasons.append(f"{invalid_share * 100:.0f}% of the readings in the last seconds are invalid{why}. "
                       f"{sensor_specs.invalid_hint(main, spec)}")
        found.append(f"{invalid_share * 100:.0f}% of the readings are invalid")
    if finite.size:
        where = float(np.median(finite))
        zone = spec.zone(where)
        if zone.name != "standard":
            reasons.append(f"The target is {zone.text}: it reads {cfg.pos(where)}. Linearity "
                           f"and repeatability of the data sheet apply inside {spec.range_text} "
                           f"{sensor_advice.cite('linearity')}.")
            found.append({"before": "the target is closer than the measuring range",
                          "after": "the target is beyond the measuring range",
                          "outside": "the target is outside even the extended measuring range"}[zone.name])
    if noise is not None and noise > linearity:
        reasons.append(f"Noise is {noise:.2f} {unit}, {noise / linearity:.1f} times the data sheet linearity of "
                       f"±{linearity:g} {unit}. The linearity is given with median 9 + moving average 4096 "
                       f"{sensor_advice.cite('linearity')}; the quick setting static is median 9 + moving average "
                       f"128, balanced is 64 {sensor_advice.cite('presets')}.")
        reasons.append("Averaging N values lowers random noise by about the square root of N, and slows the sensor "
                       "down. Choose it in the sensor's web page under Home > Signal quality; a moving average "
                       f"can go up to 4096 {sensor_advice.cite('averaging')}. The cockpit cannot change the "
                       "sensor's settings.")
        found.append(f"noise {noise:.2f} {unit} against a data sheet linearity of ±{linearity:g} {unit}")

    if noise is None:
        summary, status = f"{cfg.name}: not enough data yet.", "ok"
    elif reasons:
        summary, status = f"{cfg.name} ({spec.model}): {'; '.join(found)}. See what to check below.", "advice"
    else:
        summary, status = (f"{cfg.name} ({spec.model}) looks fine: noise {noise:.2f} {unit} is within the data sheet "
                           f"linearity of ±{linearity:g} {unit}, and the target is inside the measuring range."), "ok"

    t, _, window = engine.buffers[sid].fast_window(PREVIEW_S)
    view, _ = decimate_minmax(window.astype(float), 500)
    return {
        "sensor": sid, "name": cfg.name, "unit": unit, "status": status, "summary": summary,
        "basis": f"Data sheet linearity (±{linearity:g} {unit})",
        "current": {},
        "measured": {
            "noise": round_or_none(noise, 3),
            "normal_noise": round_or_none(baseline, 3),
            "noise_limit": round(linearity, 3),
            "noise_target": round(linearity / 3.0, 3),
            "invalid_share": round(invalid_share, 4),
            "watch_frequency_hz": None,
            "bandwidth_hz": None,
        },
        "proposal": [],
        "reasons": reasons,
        "expected": {"noise": None, "bandwidth_hz": None},
        "preview": {
            "window_s": PREVIEW_S,
            "before": [round_or_none(float(v), 3) for v in view],
            "after": [round_or_none(float(v), 3) for v in view],
        },
        "t": now,
    }


def suggest(engine: Engine, sensor: str) -> dict[str, Any]:
    """Measure the signal and propose settings. Nothing is changed here."""
    sid = engine.resolve_sensor(sensor)
    cfg = engine.settings.sensor(sid)
    values = engine.values[sid]
    now = time.time()
    unit = cfg.unit
    if cfg.spec is not None and not cfg.settings:
        return _datasheet_check(engine, sid, now)

    _, _, recent = engine.buffers[sid].fast_window(3.0)
    noise = robust_sigma(recent)
    baseline = engine.detector.baseline(sid)
    invalid_share = float(np.mean(~np.isfinite(recent))) if recent.size else 0.0
    width = cfg.tolerance.high - cfg.tolerance.low
    target = width * NOISE_TARGET_SHARE
    limit = width * NOISE_LIMIT_SHARE
    watch_hz, watch_reason = _watch_frequency(engine, sid, now)

    avg_spec = cfg.settings.get("averaging")
    rate_spec = cfg.settings.get("measuring_rate_hz")
    exp_spec = cfg.settings.get("exposure_mode")
    averaging = float(values.get("averaging", 1) or 1)
    rate = float(values.get("measuring_rate_hz", 0) or 0)

    proposal: list[dict[str, Any]] = []
    reasons: list[str] = []
    new_avg, new_rate = averaging, rate

    if noise is not None and avg_spec is not None and noise > limit:
        needed = averaging * (noise / target) ** 2
        new_avg = float(_pow2_at_least(needed, avg_spec.minimum or 1, avg_spec.maximum or 1024))
        reasons.append(
            f"Noise is {noise:.2f} {unit}, above the {limit:.2f} {unit} a "
            f"{width:g} {unit} tolerance band allows. Averaging {int(new_avg)} instead of "
            f"{int(averaging)} lowers it by about {math.sqrt(new_avg / averaging):.1f} times.")
        if rate_spec is not None and rate_spec.options and rate > 0:
            needed_rate = 2.0 * watch_hz * new_avg
            if rate / new_avg < 2.0 * watch_hz:
                faster = [float(o) for o in rate_spec.options if float(o) >= needed_rate]
                if faster:
                    new_rate = min(faster)
                    reasons.append(
                        f"With that much averaging the sensor would react too slowly to see "
                        f"{watch_hz:.0f} Hz ({watch_reason}). A measuring rate of {new_rate:g} Hz "
                        f"keeps it visible.")
                else:
                    while new_avg > averaging and rate / new_avg < 2.0 * watch_hz:
                        new_avg /= 2
                    reasons.append(
                        f"Averaging is capped at {int(new_avg)} so {watch_hz:.0f} Hz "
                        f"({watch_reason}) stays visible at {rate:g} Hz.")
        if new_avg != averaging:
            proposal.append({"name": "averaging", "from": int(averaging), "to": int(new_avg)})
        if new_rate != rate:
            proposal.append({"name": "measuring_rate_hz", "from": int(rate), "to": int(new_rate)})

    if (exp_spec is not None and exp_spec.options and "intelligent" in exp_spec.options
            and invalid_share > 0.01 and values.get("exposure_mode") != "intelligent"):
        proposal.append({"name": "exposure_mode", "from": values.get("exposure_mode"), "to": "intelligent"})
        reasons.append(f"{invalid_share * 100:.0f}% of readings are invalid. Intelligent exposure "
                       f"adapts to shiny or dark surfaces.")

    expected_noise = noise * math.sqrt(averaging / new_avg) if noise is not None else None
    bandwidth_now = rate / averaging if rate else None
    bandwidth_new = new_rate / new_avg if new_rate else None

    if proposal:
        changes = " and ".join(
            f"{p['name'].replace('_hz', '').replace('_', ' ')} to {p['to']}" for p in proposal)
        summary = (f"{cfg.name}: set {changes}. Expected noise {expected_noise:.2f} {unit} "
                   f"instead of {noise:.2f} {unit}." if noise is not None and expected_noise is not None
                   else f"{cfg.name}: set {changes}.")
        status = "improve"
    else:
        summary = (f"{cfg.name} is set up well: noise {noise:.2f} {unit} is within the "
                   f"{limit:.2f} {unit} limit." if noise is not None
                   else f"{cfg.name}: not enough data yet.")
        status = "ok"

    # Preview on real data: the last seconds as they are, and smoothed the way the
    # proposed averaging would smooth them (relative to the current averaging).
    t, _, window = engine.buffers[sid].fast_window(PREVIEW_S)
    factor = max(1, int(round(new_avg / averaging))) if new_avg >= averaging else 1
    before = window.astype(float)
    after = _moving_average(before, factor) if factor > 1 and before.size else before
    b_dec, _ = decimate_minmax(before, 500)
    a_dec, _ = decimate_minmax(after, 500)

    return {
        "sensor": sid, "name": cfg.name, "unit": unit, "status": status, "summary": summary,
        "current": {k: values.get(k) for k in cfg.settings},
        "measured": {
            "noise": round_or_none(noise, 3),
            "normal_noise": round_or_none(baseline, 3),
            "noise_limit": round(limit, 3),
            "noise_target": round(target, 3),
            "invalid_share": round(invalid_share, 4),
            "watch_frequency_hz": round(watch_hz, 1),
            "bandwidth_hz": round_or_none(bandwidth_now, 1),
        },
        "proposal": proposal,
        "reasons": reasons,
        "expected": {"noise": round_or_none(expected_noise, 3),
                     "bandwidth_hz": round_or_none(bandwidth_new, 1)},
        "preview": {
            "window_s": PREVIEW_S,
            "before": [round_or_none(float(v), 3) for v in b_dec],
            "after": [round_or_none(float(v), 3) for v in a_dec],
        },
        "t": now,
    }


def apply(engine: Engine, sensor: str) -> dict[str, Any]:
    """Apply the current proposal. Callers must have the operator's confirmation."""
    plan = suggest(engine, sensor)
    applied = []
    for change in plan["proposal"]:
        try:
            applied.append(engine.change_setting(plan["sensor"], change["name"], change["to"]))
        except EngineError as exc:
            return {"ok": False, "error": str(exc), "applied": applied}
    return {"ok": True, "applied": applied, "summary": plan["summary"] if applied else "Nothing to change."}
