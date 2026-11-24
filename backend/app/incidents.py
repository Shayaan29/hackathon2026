"""Investigate a whole recording.

The cockpit's own alert rules run over every second of the file, in fast-forward,
exactly as they would run live. Each alert becomes an incident with its evidence,
the checks it passed, the explanation, the suggested checks and a copy of the
signal around it, so an incident stays readable after the file is gone.
"""

from __future__ import annotations

import csv
import io
import math
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from .buffer import SensorBuffer
from .config import DetectionConfig, Settings
from .detection import Detector, Event, Metrics
from .ingest import MICRO, Recording
from .investigate import InvestigationContext, clock, investigate

SNAP_BEFORE_S = 3.0
SNAP_AFTER_S = 3.5
GROUP_GAP_S = 3.0  # alerts on the same sensors this close together are one incident
MAX_INCIDENTS = 500
SEVERITY_RANK = {"critical": 2, "warning": 1}


@dataclass
class _Hit:
    event: Event
    metrics: dict[str, Metrics]
    baselines: dict[str, float | None]


@dataclass
class _Group:
    hits: list[_Hit] = field(default_factory=list)
    sensors: set[str] = field(default_factory=set)
    t_end: float = 0.0


@dataclass
class Analysis:
    incidents: list[dict[str, Any]]
    total: int
    seconds: float
    evaluations: int
    elapsed_s: float
    alerts: int
    warnings: list[str]

    def stats(self) -> dict[str, Any]:
        by_kind: dict[str, int] = {}
        for incident in self.incidents:
            by_kind[incident["kind"]] = by_kind.get(incident["kind"], 0) + 1
        return {"incidents": self.total, "shown": len(self.incidents), "alerts": self.alerts,
                "seconds_analysed": round(self.seconds, 2), "evaluations": self.evaluations,
                "elapsed_s": round(self.elapsed_s, 2), "by_kind": by_kind}


def window_for(kind: str, cfg: DetectionConfig) -> float:
    """How far back the rule looked when it fired: the shaded part of the chart."""
    return {"vibration": cfg.vibration_window_s, "noise": cfg.noise_window_s, "drift": cfg.drift_window_s,
            "out_of_range": cfg.out_of_range_window_s, "invalid": cfg.invalid_window_s}.get(kind, 1.0)


def _sensors_of(event: Event) -> set[str]:
    return {event.sensor, *event.related_sensors}


def _group(hits: list[_Hit]) -> list[_Group]:
    groups: list[_Group] = []
    for hit in sorted(hits, key=lambda h: h.event.t):
        target = None
        for group in groups:
            if hit.event.t - group.t_end <= GROUP_GAP_S and group.sensors & _sensors_of(hit.event):
                target = group
                break
        if target is None:
            target = _Group()
            groups.append(target)
        target.hits.append(hit)
        target.sensors |= _sensors_of(hit.event)
        target.t_end = hit.event.t
    return groups


def _primary(group: _Group) -> _Hit:
    """The most severe alert of the incident, the earliest one if they are equal."""
    return max(group.hits, key=lambda h: (SEVERITY_RANK.get(h.event.severity, 0), -h.event.t))


def _json_values(values: np.ndarray) -> list[float | None]:
    return [round(float(v), 3) if math.isfinite(v) else None for v in values]


def analyse(recording: Recording, settings: Settings, *, playbook: dict[str, list[dict[str, Any]]],
            part_rpm: float, source: dict[str, Any],
            progress: Callable[[float, str], None] | None = None) -> Analysis:
    started = time.perf_counter()
    rate = recording.rate_hz
    sensors = [dict(s) for s in recording.sensors]
    ids = [s["id"] for s in sensors]
    series = recording.values
    n = int(series[ids[0]].size)
    seconds = n / rate
    warnings: list[str] = []
    if seconds < settings.detection.baseline_s:
        warnings.append(f"The recording is {seconds:.0f} s long. The rules learn normal noise from the first "
                        f"{settings.detection.baseline_s:g} s, so no alert can be raised before that.")

    detector = Detector(recording.sensor_configs(), settings.detection, rate)
    buffers = {sid: SensorBuffer(rate, int(seconds) + 2, int(seconds) + 3) for sid in ids}
    step = max(1, int(round(settings.detection.eval_interval_s * rate)))
    hits: list[_Hit] = []
    evaluations = 0
    pos = 0
    while pos < n:
        end = min(n, pos + step)
        t_end = (end - 1) / rate
        for sid in ids:
            buffers[sid].append(series[sid][pos:end], t_end)
        events = detector.evaluate(buffers, t_end)
        evaluations += 1
        if events:
            metrics = dict(detector.last_metrics)
            baselines = {sid: detector.baseline(sid) for sid in ids}
            hits.extend(_Hit(event, metrics, baselines) for event in events)
        pos = end
        if progress and evaluations % 100 == 0:
            progress(0.05 + 0.85 * pos / n, "Running the alert rules over the recording")

    groups = _group(hits)
    primaries = [_primary(group) for group in groups]
    times: dict[tuple[str, str], list[float]] = {}
    for primary in primaries:
        times.setdefault((primary.event.kind, primary.event.sensor), []).append(primary.event.t)

    slot_count = len(settings.sensors)
    incidents: list[dict[str, Any]] = []
    for number, (group, primary) in enumerate(zip(groups, primaries), start=1):
        if number > MAX_INCIDENTS:
            break
        ev = primary.event
        kinds = list(dict.fromkeys(h.event.kind for h in group.hits))
        same = sorted(times[(ev.kind, ev.sensor)])
        count = len(same)
        recurrence = {"count": count,
                      "median_interval_s": round(float(np.median(np.diff(same))), 2) if count >= 2 else None}
        extras: list[tuple[Event, dict[str, Metrics]]] = []
        seen_kinds = {ev.kind}
        for hit in group.hits:  # the first alert of every other rule in this incident
            if hit.event.kind not in seen_kinds:
                seen_kinds.add(hit.event.kind)
                extras.append((hit.event, hit.metrics))
        ctx = InvestigationContext(
            event=ev, kinds=kinds, metrics=primary.metrics, baselines=primary.baselines, series=series,
            sensors=sensors, rate_hz=rate, detection=settings.detection, rpm=part_rpm,
            t_text=clock(ev.t), recurrence=recurrence, slot_count=slot_count, playbook=playbook,
            extras=extras)
        result = investigate(ctx)
        i0 = max(0, int(round((ev.t - SNAP_BEFORE_S) * rate)))
        i1 = min(n, int(round((ev.t + SNAP_AFTER_S) * rate)))
        window = window_for(ev.kind, settings.detection)
        incidents.append({
            "id": f"INC-{number:03d}",
            "title": result["headline"],
            "kind": ev.kind,
            "kinds": kinds,
            "severity": ev.severity,
            "status": "open",
            "t": round(ev.t, 4),
            "t_text": clock(ev.t),
            "sensor": ev.sensor,
            "sensor_name": next(s["name"] for s in sensors if s["id"] == ev.sensor),
            "related": list(ev.related_sensors),
            "summary": ev.summary,
            "numbers": ev.numbers,
            "forecast_min": ev.forecast_min,
            "highlight": {"from_s": round(max(0.0, ev.t - window), 4), "to_s": round(ev.t, 4)},
            "window": {"from_s": round(i0 / rate, 4), "to_s": round(i1 / rate, 4)},
            "checks": result["checks"],
            "explanation": result["explanation"],
            "suggestions": result["suggestions"],
            "unknown": result["unknown"],
            "location": result["location"],
            "recurrence": recurrence,
            "snapshot": {
                "t0": round(i0 / rate, 4), "rate_hz": rate,
                "sensors": [{"id": s["id"], "name": s["name"], "unit": MICRO, "tolerance": s["tolerance"],
                             "values": _json_values(series[s["id"]][i0:i1])} for s in sensors],
            },
            "source": source,
        })
    if len(groups) > MAX_INCIDENTS:
        warnings.append(f"Only the first {MAX_INCIDENTS} of {len(groups)} incidents are kept.")
    if progress:
        progress(1.0, "Done")
    return Analysis(incidents=incidents, total=len(groups), seconds=seconds, evaluations=evaluations,
                    elapsed_s=time.perf_counter() - started, alerts=len(hits), warnings=warnings)


def to_row(incident: dict[str, Any]) -> dict[str, Any]:
    """The short form shown in the incident list and returned to the copilot."""
    return {"id": incident["id"], "title": incident["title"], "kind": incident["kind"],
            "kinds": incident["kinds"], "severity": incident["severity"], "status": incident["status"],
            "t": incident["t"], "t_text": incident["t_text"], "sensor": incident["sensor"],
            "sensor_name": incident["sensor_name"], "related": incident["related"],
            "repeats": incident["recurrence"]["count"], "summary": incident["summary"]}


def excerpt_csv(incident: dict[str, Any]) -> str:
    """The snapshot as CSV: time in seconds from the start of the recording, one column per sensor."""
    snap = incident["snapshot"]
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["time_s", *[f"{s['name']} [{s['unit']}]" for s in snap["sensors"]]])
    length = max((len(s["values"]) for s in snap["sensors"]), default=0)
    for i in range(length):
        t = snap["t0"] + i / snap["rate_hz"]
        row = [f"{t:.4f}".replace(".", ",")]
        for sensor in snap["sensors"]:
            value = sensor["values"][i] if i < len(sensor["values"]) else None
            row.append("" if value is None else f"{value:.3f}".replace(".", ","))
        writer.writerow(row)
    return buffer.getvalue()


class IncidentStore:
    """The incidents of the current analysis, in time order."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []
        self.stats: dict[str, Any] | None = None
        self.source: dict[str, Any] | None = None
        self.warnings: list[str] = []
        self._by_id: dict[str, dict[str, Any]] = {}

    def replace(self, analysis: Analysis, source: dict[str, Any]) -> None:
        self.items = analysis.incidents
        self.stats = analysis.stats()
        self.source = source
        self.warnings = analysis.warnings
        self._by_id = {item["id"]: item for item in self.items}

    def clear(self) -> None:
        self.items, self.stats, self.source, self.warnings = [], None, None, []
        self._by_id = {}

    def get(self, incident_id: str) -> dict[str, Any] | None:
        return self._by_id.get(incident_id)

    def acknowledge(self, incident_id: str) -> dict[str, Any] | None:
        incident = self.get(incident_id)
        if incident is not None and incident["status"] == "open":
            incident["status"] = "acknowledged"
        return incident

    def rows(self) -> list[dict[str, Any]]:
        return [to_row(item) for item in self.items]
