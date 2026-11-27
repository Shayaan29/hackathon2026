"""The AI tools that run on the backend.

The voice model calls these through the browser. They answer with small,
rounded summaries the model can read out loud, never raw samples. Every
number the copilot says comes from here.
"""

from __future__ import annotations

import time
from typing import Any

import numpy as np

from .analysis import (best_trend, minutes_to_limit, robust_sigma, round_or_none,
                       spectral_peak, summary)
from .engine import Engine, EngineError


def get_data(engine: Engine, sensor: str, window_s: float = 10.0) -> dict[str, Any]:
    sid = engine.resolve_sensor(sensor)
    cfg = engine.settings.sensor(sid)
    window_s = float(min(max(window_s, 1.0), engine.settings.engine.fast_buffer_s))
    _, _, values = engine.buffers[sid].fast_window(window_s)
    stats = summary(values)
    noise = robust_sigma(values)
    baseline = engine.detector.baseline(sid)
    latest = stats["latest"]
    result = {
        "sensor": sid,
        "name": cfg.name,
        "model": cfg.model,
        "unit": cfg.unit,
        "window_s": window_s,
        "latest": round_or_none(latest, 2),
        "mean": round_or_none(stats["mean"], 2),
        "min": round_or_none(stats["min"], 2),
        "max": round_or_none(stats["max"], 2),
        "noise": round_or_none(noise, 3),
        "normal_noise": round_or_none(baseline, 3),
        "valid_percent": round(stats["valid_ratio"] * 100.0, 1),
        "tolerance": cfg.tolerance.as_dict(),
        "within_tolerance": None if latest is None else cfg.tolerance.contains(latest),
        "state": engine.sensor_state(sid),
        "active_alerts": engine.detector.active_kinds(sid),
        "settings": dict(engine.values[sid]),
        "source": engine.source_info.get(sid, {}).get("type"),
    }
    if cfg.display is not None:
        # a sensor that reports millimetres: say positions the way its own web page does
        def where(value: float | None) -> str | None:
            return None if value is None else cfg.pos(value)

        zone = cfg.spec.zone(latest) if cfg.spec is not None and latest is not None else None
        result["position"] = {
            "latest": where(latest), "mean": where(stats["mean"]), "min": where(stats["min"]),
            "max": where(stats["max"]),
            "limits": f"{cfg.pos(cfg.tolerance.low)} to {cfg.pos(cfg.tolerance.high)} ({cfg.limit_word})",
            "where": zone.text if zone else None,
            "state_led": zone.led if zone else None,
            "note": "Positions are distances from the start of the measuring range, in millimetres. Noise, "
                    "peaks and drift rates are in micrometres.",
        }
    if cfg.spec is not None:
        result["datasheet"] = cfg.spec.summary()
    return result


def analyze(engine: Engine, sensor: str, window_s: float = 60.0) -> dict[str, Any]:
    sid = engine.resolve_sensor(sensor)
    cfg = engine.settings.sensor(sid)
    det = engine.settings.detection
    buf = engine.buffers[sid]
    now = time.time()
    result = get_data(engine, sid, min(window_s, 30.0))
    result["window_s"] = window_s
    ongoing: list[str] = []
    past: list[str] = []

    # Drift: the steepest straight trend over several look-back windows.
    slow = buf.slow_window(max(window_s, 60.0))
    windows = tuple(w for w in (15.0, 30.0, 60.0, 120.0, 300.0, 600.0) if w <= max(window_s, 15.0))
    trend = best_trend(slow["t"], slow["mean"], windows, det.drift_min_r2, det.drift_min_rate_per_min,
                       t_end=buf.t_last)
    if trend is not None:
        minutes = minutes_to_limit(trend["current"], trend["rate_per_min"],
                                   cfg.tolerance.low, cfg.tolerance.high)
        result["trend"] = {
            "drifting": True,
            "rate_per_min": round(trend["rate_per_min"], 2),
            "straightness_r2": round(trend["r2"], 2),
            "minutes_to_tolerance_limit": round_or_none(minutes, 1),
        }
        text = f"Steady drift of {trend['rate_per_min']:+.1f} {cfg.unit} per minute"
        if minutes is not None:
            limit = "the end of the measuring range" if cfg.tolerance_kind == "measuring_range" \
                else "the tolerance limit"
            text += f", reaching {limit} in about {minutes:.0f} minutes"
        ongoing.append(text + ".")
    else:
        result["trend"] = {"drifting": False}

    if result["within_tolerance"] is False:
        if cfg.tolerance_kind == "measuring_range" and cfg.spec is not None and result["latest"] is not None:
            zone = cfg.spec.zone(result["latest"])
            ongoing.insert(0, f"The target is currently {zone.text}, at {cfg.pos(result['latest'])}.")
        else:
            ongoing.insert(0, "The value is currently outside the tolerance band.")

    _, _, recent = buf.fast_window(1.0)
    invalid_share = float(np.mean(~np.isfinite(recent))) if recent.size else 0.0
    if invalid_share > 0:
        ongoing.append(f"{invalid_share * 100:.0f}% of readings in the last second were invalid.")

    # Last vibration this sensor took part in, measured on this sensor's own data.
    vib = engine.last_event("vibration", sid)
    if vib is not None and vib.t >= now - window_s:
        _, _, burst = buf.fast_range(vib.t - 1.0, vib.t + 1.0)
        peak = spectral_peak(burst, engine.rate_hz, fmin=engine.settings.detection.vibration_min_hz, fmax=100.0)
        ok = burst[np.isfinite(burst)]
        peak_dev = float(np.max(np.abs(ok - np.median(ok)))) if ok.size else None
        others = vib.related_sensors if vib.sensor == sid else [vib.sensor] + [
            s for s in vib.related_sensors if s != sid]
        result["last_vibration"] = {
            "seconds_ago": round(now - vib.t, 1),
            "frequency_hz": round_or_none(peak["frequency_hz"] if peak else None, 1),
            "peak": round_or_none(peak_dev, 1),
            "clean_single_frequency": bool(peak and peak["prominence"] >= 8.0),
            "also_seen_by": others,
        }
        if peak and peak_dev is not None:
            past.append(f"Vibration {now - vib.t:.0f} s ago at {peak['frequency_hz']:.0f} Hz, "
                        f"{peak_dev:.1f} {cfg.unit} peak on this sensor.")
        if others:
            names = ", ".join(engine.settings.sensor(s).name for s in others)
            past.append(f"The same vibration appeared on {names} at the same moment.")
    else:
        _, _, window = buf.fast_window(min(window_s, 4.0))
        peak = spectral_peak(window, engine.rate_hz, fmin=engine.settings.detection.vibration_min_hz, fmax=100.0)
        if peak and peak["prominence"] >= 8.0:
            result["dominant_frequency_hz"] = round(peak["frequency_hz"], 1)

    observations = ongoing + past
    if not observations:
        observations.append("Nothing unusual: noise and position are normal.")
    result["recent_events"] = [
        {"kind": e["kind"], "severity": e["severity"], "seconds_ago": round(now - e["t"], 1),
         "summary": e["summary"]}
        for e in engine.recent_events(limit=5, since=now - window_s, sensor=sid)
    ]
    result["observations"] = observations
    return result


def change_setting(engine: Engine, sensor: str, name: str, value: Any,
                   confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        return {"ok": False, "error": "Not confirmed. Ask the operator to confirm this exact change first."}
    try:
        return engine.change_setting(sensor, name, value)
    except EngineError as exc:
        return {"ok": False, "error": str(exc)}
