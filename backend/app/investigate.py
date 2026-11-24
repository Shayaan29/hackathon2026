"""Investigate one alert: the checks it ran, the explanation Epsilon gives and the
suggested checks.

Everything shown is computed from the recording and from the rules' own thresholds.
The cockpit never names a cause. Suggestions are checks to carry out, and each one
is shown with the measured numbers that made it relevant. Anything the file does not
tell us is listed as unknown instead of being filled in.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from .config import ConfigError, DetectionConfig
from .detection import Event, Metrics
from .geometry import twin_side, track_angle

PLAYBOOK_PATH = Path(__file__).resolve().parent.parent / "config" / "playbook.yaml"
RULE_LABELS = {"vibration": "Vibration", "noise": "Noise", "drift": "Drift",
               "out_of_range": "Out of tolerance", "invalid": "Invalid readings"}
PLAYBOOK_KEYS = (*RULE_LABELS, "all")


class _Facts(dict):
    def __missing__(self, key: str) -> str:
        return "unknown"


@dataclass
class InvestigationContext:
    event: Event
    kinds: list[str]                       # every rule that fired in this incident
    metrics: dict[str, Metrics]            # what the rules measured at that moment
    baselines: dict[str, float | None]     # normal noise per sensor at that moment
    series: dict[str, np.ndarray]          # the whole recording, one array per sensor
    sensors: list[dict[str, Any]]          # id, name, unit, tolerance, tolerance_source, fixture
    rate_hz: int
    detection: DetectionConfig
    rpm: float                             # from the mapping only; 0 means unknown
    t_text: str
    recurrence: dict[str, Any]
    slot_count: int                        # sensor positions the 3D twin knows
    playbook: dict[str, list[dict[str, Any]]]
    # other rules of the same incident, each with what it measured at its own moment
    extras: list[tuple[Event, dict[str, Metrics]]] = field(default_factory=list)


def load_playbook(path: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    path = Path(path or PLAYBOOK_PATH)
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as handle:
            doc = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name} is not valid YAML: {exc}") from exc
    book: dict[str, list[dict[str, Any]]] = {}
    for key, entries in doc.items():
        if key not in PLAYBOOK_KEYS:
            raise ConfigError(f"{path.name}: unknown section {key!r}; use one of {', '.join(PLAYBOOK_KEYS)}")
        items = []
        for entry in entries or []:
            if not isinstance(entry, dict) or not entry.get("text"):
                raise ConfigError(f"{path.name}: every entry under {key!r} needs a text")
            items.append({"text": str(entry["text"]), "reason": str(entry.get("reason", "")),
                          "needs_frequency": bool(entry.get("needs_frequency", False))})
        book[key] = items
    return book


def fill(template: str, facts: dict[str, str]) -> str:
    return template.format_map(_Facts(facts))


def clock(t: float) -> str:
    """Recording time as mm:ss.mmm, for the explanation and the incident list."""
    minutes = int(t // 60)
    return f"{minutes:02d}:{t - minutes * 60:06.3f}"


def _num(value: Any, digits: int = 1, signed: bool = False) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if not math.isfinite(number):
        return "unknown"
    return f"{number:+.{digits}f}" if signed else f"{number:.{digits}f}"


def _check(key: str, label: str, value: str, status: str, detail: str = "") -> dict[str, str]:
    """status: found (the rule's condition holds), not_found, info (context), unknown (not in the file)."""
    return {"key": key, "label": label, "value": value, "status": status, "detail": detail}


def _rule_detail(kind: str, cfg: DetectionConfig, unit: str) -> str:
    return {
        "vibration": (f"short-window spread at least {cfg.vibration_ratio:g} times normal noise, "
                      f"with one clear peak above {cfg.vibration_min_hz:g} Hz"),
        "noise": (f"spread at least {cfg.noise_ratio:g} times normal for {cfg.noise_hold_s:g} s, "
                  "without one clear frequency"),
        "drift": f"steady trend of at least {cfg.drift_min_rate_per_min:g} {unit} per minute",
        "out_of_range": "the averaged value left the tolerance band",
        "invalid": f"at least {cfg.invalid_min_share * 100:.0f} % of the readings invalid in one second",
    }.get(kind, "")


def _kind_checks(kind: str, event: Event, cfg: DetectionConfig, unit: str, metrics: Metrics | None) -> list[dict]:
    n = event.numbers
    if kind == "vibration":
        out = []
        if metrics is not None and metrics.peak:
            out.append(_check("frequency", "Dominant frequency", f"{_num(n.get('frequency_hz'))} Hz", "found",
                              f"one clear peak, prominence {metrics.peak['prominence']:.1f} "
                              f"(rule needs {cfg.vibration_min_prominence:g})"))
        out.append(_check("amplitude", "Peak deviation", f"{_num(n.get('peak'))} {unit}", "found",
                          f"{_num(n.get('times_normal_noise'))} times normal noise over the last "
                          f"{cfg.vibration_window_s:g} s"))
        return out
    if kind == "noise":
        return [_check("noise_level", "Noise level", f"{_num(n.get('noise'), 2)} {unit}", "found",
                       f"{_num(n.get('times_normal_noise'))} times normal, held for {cfg.noise_hold_s:g} s, "
                       "no single clear frequency")]
    if kind == "drift":
        out = [_check("rate", "Drift rate", f"{_num(n.get('rate_per_min'), 2, True)} {unit} per minute", "found",
                      f"straight-line fit, R² {_num(n.get('r2'), 2)} (rule needs {cfg.drift_min_r2:g})")]
        if event.forecast_min is not None:
            out.append(_check("forecast", "Time to the tolerance limit", f"about {event.forecast_min:.0f} min",
                              "found", "if the rate holds"))
        else:
            out.append(_check("forecast", "Time to the tolerance limit", "not reached at this rate", "info",
                              "the trend does not lead to a limit"))
        return out
    if kind == "out_of_range":
        return [_check("value", "Averaged value", f"{_num(n.get('value'), 1, True)} {unit}", "found",
                       f"limit {_num(n.get('limit'), 1, True)} {unit}")]
    if kind == "invalid":
        return [_check("invalid", "Invalid readings in the last second", f"{n.get('invalid_percent', 'unknown')} %",
                       "found", f"rule needs at least {cfg.invalid_min_share * 100:.0f} %")]
    return []


def _segment(values: np.ndarray, t0: float, t1: float, rate: int) -> np.ndarray:
    i0 = max(0, int(round(t0 * rate)))
    i1 = min(values.size, int(round(t1 * rate)))
    return values[i0:i1]


def _residuals(seg: np.ndarray) -> np.ndarray | None:
    """Same-length array with the straight-line trend removed; invalid samples are zero."""
    finite = np.isfinite(seg)
    if int(finite.sum()) < 8:
        return None
    x = np.arange(seg.size, dtype=float)
    slope, intercept = np.polyfit(x[finite], seg[finite], 1)
    out = np.zeros(seg.size)
    out[finite] = seg[finite] - (slope * x[finite] + intercept)
    return out


def _level_step(values: np.ndarray, t: float, window_s: float, rate: int) -> float | None:
    """How far the mean moved between the first and second half of the rule's window."""
    seg = _segment(values, t - window_s, t, rate)
    finite = seg[np.isfinite(seg)]
    if finite.size < 20:
        return None
    half = finite.size // 2
    return float(abs(finite[half:].mean() - finite[:half].mean()))


def _vibration_step(event: Event, ctx: "InvestigationContext", unit: str, base: float | None) -> tuple[dict, str] | None:
    """A large level step inside the vibration window can look like vibration. Say so."""
    if event.kind != "vibration" or not base:
        return None
    shift = _level_step(ctx.series[event.sensor], event.t, ctx.detection.vibration_window_s, ctx.rate_hz)
    if shift is None or shift <= 3 * base:
        return None
    check = _check("level_step", "Level change inside the window", f"{shift:.1f} {unit}", "info",
                   "a step of this size can trip the vibration rule; check the plot before trusting the frequency")
    line = (f"The level also moved by {shift:.1f} {unit} inside the same second. A step like this can trip the "
            "vibration rule, so check the plot before trusting the frequency.")
    return check, line


def _time_of_peak(values: np.ndarray, t: float, rate: int, half_s: float = 0.5) -> float:
    seg = _segment(values, t - half_s, t + half_s, rate)
    finite = np.isfinite(seg)
    if not finite.any():
        return t
    i0 = max(0, int(round((t - half_s) * rate)))
    centre = float(np.median(seg[finite]))
    dev = np.where(finite, np.abs(seg - centre), -np.inf)
    return (i0 + int(np.argmax(dev))) / rate


def _cross_sensor(name: str, base: float | None, series_event: np.ndarray, series_other: np.ndarray,
                  t: float, rate: int, window_s: float, noise_ratio: float,
                  max_lag_s: float = 0.05) -> dict[str, Any]:
    """Did the other sensor move in the same window, and how far behind the event sensor?

    The lag is searched only within ``max_lag_s``. For a periodic signal a larger
    lag is ambiguous (every period looks alike), so the caller limits it to half a
    period of the dominant frequency."""
    seg = _segment(series_other, t - window_s, t, rate)
    res = _residuals(seg)
    if res is None or not base:
        return {"status": "unknown", "ratio": None, "lag_s": None, "name": name}
    ratio = float(np.std(res[np.isfinite(seg)])) / base
    if ratio < noise_ratio:
        return {"status": "not_found", "ratio": ratio, "lag_s": None, "name": name}
    lag_s = None
    a = _residuals(_segment(series_event, t - 0.5, t + 0.5, rate))
    b = _residuals(_segment(series_other, t - 0.5, t + 0.5, rate))
    if a is not None and b is not None and a.size == b.size and a.size > 40:
        max_lag = int(max_lag_s * rate)
        best_c, best_lag = -math.inf, 0
        for lag in range(-max_lag, max_lag + 1):
            if lag >= 0:
                x, y = a[: a.size - lag], b[lag:]
            else:
                x, y = a[-lag:], b[: b.size + lag]
            denom = float(np.linalg.norm(x) * np.linalg.norm(y))
            if x.size < 10 or denom == 0:
                continue
            c = float(np.dot(x, y)) / denom
            if c > best_c:
                best_c, best_lag = c, lag
        if best_c > -math.inf:
            lag_s = best_lag / rate
    return {"status": "found", "ratio": ratio, "lag_s": lag_s, "name": name}


def investigate(ctx: InvestigationContext) -> dict[str, Any]:
    ev = ctx.event
    cfg = ctx.detection
    info = {s["id"]: s for s in ctx.sensors}
    me = info[ev.sensor]
    name, unit = me["name"], me["unit"]
    tol = me["tolerance"]
    metrics = ctx.metrics.get(ev.sensor)
    base = ctx.baselines.get(ev.sensor)
    label = " + ".join(RULE_LABELS[k] for k in ctx.kinds)
    freq = ev.numbers.get("frequency_hz") if ev.kind == "vibration" else None

    checks: list[dict] = [_check("rule", "Rule that fired", label, "found", _rule_detail(ev.kind, cfg, unit))]
    checks.extend(_kind_checks(ev.kind, ev, cfg, unit, metrics))
    step_lines: list[str] = []
    step = _vibration_step(ev, ctx, unit, base)
    if step is not None:
        checks.append(step[0])
        step_lines.append(step[1])
    extra_lines: list[str] = []
    for extra, extra_metrics in ctx.extras:
        extra_unit = info[extra.sensor]["unit"]
        for c in _kind_checks(extra.kind, extra, cfg, extra_unit, extra_metrics.get(extra.sensor)):
            checks.append({**c, "key": f"{extra.kind}:{c['key']}",
                           "label": f"{RULE_LABELS[extra.kind]}: {c['label']}"})
        extra_lines.append(f'The "{RULE_LABELS[extra.kind]}" rule also fired at {clock(extra.t)}: {extra.summary}')
        if extra.kind == "vibration" and "frequency_hz" in extra.numbers:
            extra_lines.append(f"Its clear frequency was {extra.numbers['frequency_hz']:.1f} Hz.")
        extra_step = _vibration_step(extra, ctx, extra_unit, ctx.baselines.get(extra.sensor))
        if extra_step is not None:
            checks.append({**extra_step[0], "key": f"vibration:{extra_step[0]['key']}",
                           "label": f"Vibration: {extra_step[0]['label']}"})
            extra_lines.append(extra_step[1])
    if base is not None:
        checks.append(_check("normal_noise", f"Normal noise, learned from the first {cfg.baseline_s:g} s",
                             f"{base:.2f} {unit}", "info", "the level the rules compare against"))
    else:
        checks.append(_check("normal_noise", "Normal noise", "unknown", "unknown", "not learned yet"))

    # the same moment on the other sensors
    window_s = {"vibration": cfg.vibration_window_s, "noise": cfg.noise_window_s}.get(ev.kind, 1.0)
    max_lag_s = 0.05 if freq is None else min(0.05, 0.5 / float(freq))
    cross: list[dict[str, Any]] = []
    for other in ctx.sensors:
        if other["id"] == ev.sensor:
            continue
        result = _cross_sensor(other["name"], ctx.baselines.get(other["id"]), ctx.series[ev.sensor],
                               ctx.series[other["id"]], ev.t, ctx.rate_hz, window_s, cfg.noise_ratio,
                               max_lag_s=max_lag_s)
        cross.append(result)
        if result["status"] == "found":
            detail = (f"lag {result['lag_s']:+.3f} s against {name}" if result["lag_s"] is not None
                      else "moved in the same window")
            checks.append(_check(f"other_{other['id']}", f"{other['name']} in the same window",
                                 f"{result['ratio']:.1f} times its normal noise", "found", detail))
        elif result["status"] == "not_found":
            checks.append(_check(f"other_{other['id']}", f"{other['name']} in the same window",
                                 f"{result['ratio']:.1f} times its normal noise", "not_found",
                                 "stayed within its normal noise"))
        else:
            checks.append(_check(f"other_{other['id']}", f"{other['name']} in the same window", "unknown",
                                 "unknown", "not enough data for this sensor"))

    rec = ctx.recurrence
    if rec.get("count", 1) >= 2:
        checks.append(_check("repeats", "Repeats in this recording", f"{rec['count']} times", "found",
                             f"about every {rec['median_interval_s']:.1f} s on {name}"))
    else:
        checks.append(_check("repeats", "Repeats in this recording", "once", "info",
                             f"no second alert of this kind on {name}"))

    tol_placeholder = me.get("tolerance_source", "placeholder") == "placeholder"
    checks.append(_check("tolerance", "Tolerance band", f"{tol['low']:+g} to {tol['high']:+g} {unit}", "info",
                         "placeholder until the team enters the real limits" if tol_placeholder
                         else "limits from the recording mapping"))

    # where on the part: the time of the largest deviation, turned into an angle when the speed is known
    peak_t = _time_of_peak(ctx.series[ev.sensor], ev.t, ctx.rate_hz)
    angle = turn = None
    if ctx.rpm > 0:
        period = 60.0 / ctx.rpm
        angle = (peak_t % period) / period * 360.0
        turn = int(peak_t // period) + 1
    slot = [s["id"] for s in ctx.sensors].index(ev.sensor)
    side = twin_side(slot, ctx.slot_count)
    if angle is not None:
        checks.append(_check("location", "Position on the part", f"{angle:.0f}° of turn {turn}", "found",
                             "counted from the first sample of the recording; the file has no once-per-turn marker"))
    else:
        checks.append(_check("location", "Position on the part", "unknown", "unknown",
                             "the part speed (rpm) is not entered in the mapping"))

    unknown: list[str] = []
    if ctx.rpm <= 0:
        unknown.append("the part speed (rpm), so the angle on the part")
    if tol_placeholder:
        unknown.append("the real tolerance limits")
    if len({s.get("fixture") for s in ctx.sensors}) == 1:
        unknown.append("which sensors share a fixture (all sensors are treated as one group)")
    if side is None:
        unknown.append(f"a 3D position for {name} (the twin has {ctx.slot_count} sensor positions)")

    lines = [f'At {ctx.t_text} the rule "{label}" fired on {name}.', ev.summary]
    lines.extend(extra_lines)
    lines.extend(step_lines)
    if freq is not None:
        lines.append(f"The vibration rule needs one clear frequency, and this one is {freq:.1f} Hz.")
    for result in cross:
        if result["status"] == "found":
            text = f"{result['name']} moved in the same window: {result['ratio']:.1f} times its normal noise"
            if result["lag_s"] is not None:
                text += f", lag {result['lag_s']:+.3f} s"
            lines.append(text + ".")
        elif result["status"] == "not_found":
            lines.append(f"{result['name']} stayed within its normal noise in that window.")
    if rec.get("count", 1) >= 2:
        lines.append(f"The same rule fired {rec['count']} times on {name}, about every "
                     f"{rec['median_interval_s']:.1f} s.")
    if angle is not None:
        lines.append(f"Measured position: {angle:.0f}° of turn {turn}, counted from the start of the recording.")
    elif side is not None:
        lines.append(f"{name} sits on the {side} side of the twin. The part speed is unknown, "
                     "so the spot on the part is not shown.")
    lines.append("This shows a pattern in the signal, not a diagnosis. The checks below say what to look at next.")
    if unknown:
        lines.append("Not known from the file: " + "; ".join(unknown) + ".")

    facts: dict[str, str] = {
        "sensor": name, "unit": unit, "t_text": ctx.t_text,
        "freq": _num(freq) if freq is not None else "unknown",
        "peak": _num(ev.numbers.get("peak")), "ratio": _num(ev.numbers.get("times_normal_noise")),
        "count": str(rec.get("count", 1)),
        "interval": _num(rec.get("median_interval_s")) if rec.get("count", 1) >= 2 else "unknown",
        "rate": _num(ev.numbers.get("rate_per_min"), 2, True),
        "minutes": _num(ev.forecast_min, 0), "value": _num(ev.numbers.get("value"), 1, True),
        "limit": _num(ev.numbers.get("limit"), 1, True), "noise": _num(ev.numbers.get("noise"), 2),
        "invalid": _num(ev.numbers.get("invalid_percent"), 0),
        "tol_low": f"{tol['low']:+g}", "tol_high": f"{tol['high']:+g}",
    }
    suggestions: list[dict[str, str]] = []
    seen: set[str] = set()
    for key in ("all", *ctx.kinds):
        for entry in ctx.playbook.get(key, []):
            if entry["needs_frequency"] and freq is None:
                continue
            text = fill(entry["text"], facts)
            if text in seen:
                continue
            seen.add(text)
            suggestions.append({"text": text, "reason": fill(entry["reason"], facts), "kind": key})

    return {
        "headline": f"{label} on {name} at {ctx.t_text}",
        "checks": checks,
        "explanation": lines,
        "suggestions": suggestions,
        "unknown": unknown,
        "location": {
            "sensor": ev.sensor, "sensor_name": name, "twin_side": side,
            "part_angle_deg": None if angle is None else round(angle, 1), "turn": turn,
            "time_of_peak_s": round(peak_t, 4), "twin_angle_deg": round(math.degrees(track_angle(slot, ctx.slot_count)), 1)
            if side is not None else None,
        },
    }
