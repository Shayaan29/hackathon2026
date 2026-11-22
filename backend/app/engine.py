"""The acquisition loop: sources -> buffers -> alert rules -> WebSocket clients.

Runs inside the FastAPI event loop. Every tick it pulls new samples from each
source, stores them, streams a peak-preserving thinned copy to the cockpit,
runs the alert rules and publishes status and events.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from collections import deque
from typing import Any

import numpy as np

from .analysis import decimate_minmax, robust_sigma, round_or_none, to_json_list
from .buffer import SensorBuffer
from .config import Settings
from .detection import Detector, Event
from .hub import COCKPIT, PHONE, Hub
from .sources import SensorSource, SimulatedSensor, SourceError, create_source
from .sources.simulator import make_history

SNAPSHOT_BEFORE_S = 3.0  # full-rate data kept around every alert, for zooming in later
SNAPSHOT_AFTER_S = 3.5
SNAPSHOTS_PER_SENSOR = 80


def track_angle(index: int, count: int) -> float:
    """Where sensor ``index`` meets the turning part. The 3D twin uses the same rule:
    the first half of the sensors sit left of the axis, the rest right of it."""
    return math.pi if count > 1 and index < count / 2 else 0.0

log = logging.getLogger("cockpit.engine")

DEMO_KINDS = ("tap", "vibration", "drift", "drift_stop", "noise", "dropout", "misconfigure", "dent", "reset")
_NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                 "eins": "1", "zwei": "2", "drei": "3", "vier": "4", "fünf": "5"}


class EngineError(ValueError):
    """A request the engine cannot fulfil (unknown sensor, invalid setting...)."""


class Engine:
    tick_s = 0.05
    display_interval_s = 0.1
    status_interval_s = 1.0
    display_points_per_s = 50

    def __init__(self, settings: Settings, hub: Hub) -> None:
        self.settings = settings
        self.hub = hub
        eng = settings.engine
        self.rate_hz = eng.analysis_rate_hz
        self.buffers = {s.id: SensorBuffer(self.rate_hz, eng.fast_buffer_s, eng.slow_buffer_s)
                        for s in settings.sensors}
        self.sources: dict[str, SensorSource] = {}
        self.source_info: dict[str, dict[str, Any]] = {}
        self.detector = Detector(settings.sensors, settings.detection, self.rate_hz)
        self.events: deque[Event] = deque(maxlen=300)
        self.values: dict[str, dict[str, Any]] = {
            s.id: {name: spec.default for name, spec in s.settings.items()} for s in settings.sensors
        }
        self._pending = {s.id: np.empty(0) for s in settings.sensors}
        self._pending_t_end: dict[str, float] = {}
        self._bucket = max(1, int(round(self.rate_hz / (self.display_points_per_s / 2))))
        self._read_errors: dict[str, str] = {}
        self._task: asyncio.Task[None] | None = None
        self.started_at = time.time()
        # earlier incidents of the shift (simulator only) and full-rate snapshots per sensor
        self.history_events: list[Event] = []
        self.snapshots: dict[str, list[tuple[float, float, np.ndarray]]] = {s.id: [] for s in settings.sensors}
        self._snapshot_due: list[tuple[float, Event]] = []

    # -- lifecycle ---------------------------------------------------------

    def start_sources(self) -> None:
        now = time.time()
        SimulatedSensor.part_omega = 2 * math.pi * self.settings.engine.part_rpm / 60.0
        count = len(self.settings.sensors)
        for index, cfg in enumerate(self.settings.sensors):
            info: dict[str, Any] = {"type": cfg.source_type, "fallback": False, "error": None}
            try:
                source = create_source(cfg, self.rate_hz)
                source.start()
            except Exception as exc:  # noqa: BLE001 - any failure falls back to dummy data
                log.error("%s: %s source failed (%s); using the simulator instead",
                          cfg.id, cfg.source_type, exc)
                source = SimulatedSensor(cfg, self.rate_hz)
                info.update(type="simulator", fallback=True, error=str(exc))
            if isinstance(source, SimulatedSensor):
                source.track_angle = track_angle(index, count)
            self.sources[cfg.id] = source
            self.source_info[cfg.id] = info
        self._load_shift_history(now)
        for cfg in self.settings.sensors:
            history = self.sources[cfg.id].backfill(self.settings.engine.backfill_s, now)
            if history is not None:
                values, t_end = history
                self.buffers[cfg.id].append(values, t_end)
            log.info("%s: %s source ready", cfg.id, self.source_info[cfg.id]["type"])

    def _load_shift_history(self, now: float) -> None:
        """Simulator only: the earlier part of the shift, so there is a whole shift to explore.
        Real sensors never get invented history."""
        eng = self.settings.engine
        sims = [s for s in self.sources.values() if isinstance(s, SimulatedSensor)]
        if len(sims) != len(self.sources) or eng.slow_buffer_s <= eng.backfill_s + 60:
            return
        end = now - eng.backfill_s
        start = now - eng.slow_buffer_s + 2
        fixtures = {s.id: s.fixture for s in self.settings.sensors}
        history = make_history(sims, fixtures, start, end)
        for sid, (t, mean, lo, hi) in history["seconds"].items():
            self.buffers[sid].append_slow(t, mean, lo, hi)
        for i, e in enumerate(history["events"]):
            self.history_events.append(Event(id=f"H{i + 1}", sensor=e["sensor"], kind=e["kind"],
                                             severity=e["severity"], t=e["t"], numbers=e["numbers"],
                                             summary=e["summary"], forecast_min=e.get("forecast_min"),
                                             related_sensors=e.get("related_sensors", [])))
        for sid, snaps in history["snapshots"].items():
            self.snapshots[sid].extend(snaps)
        log.info("shift history: %d earlier incidents over %.1f h", len(self.history_events),
                 (end - start) / 3600)

    async def start(self) -> None:
        self.start_sources()
        self._task = asyncio.create_task(self._run(), name="acquisition")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        for source in self.sources.values():
            try:
                source.stop()
            except Exception:  # noqa: BLE001
                log.exception("error while stopping a source")

    async def _run(self) -> None:
        last_display = last_eval = last_status = 0.0
        next_tick = time.monotonic()
        while True:
            now = time.time()
            self.poll(now)
            if now - last_display >= self.display_interval_s:
                self.flush_display()
                last_display = now
            if now - last_eval >= self.settings.detection.eval_interval_s:
                for event in self.detector.evaluate(self.buffers, now):
                    self.publish_event(event)
                last_eval = now
            if now - last_status >= self.status_interval_s:
                self.hub.broadcast(self.status_message())
                last_status = now
            self._take_snapshots(now)
            next_tick += self.tick_s
            await asyncio.sleep(max(0.0, next_tick - time.monotonic()))

    # -- acquisition -------------------------------------------------------

    def poll(self, now: float) -> None:
        for sid, source in self.sources.items():
            try:
                values, t_end = source.read(now)
            except Exception as exc:  # noqa: BLE001
                message = str(exc)
                if self._read_errors.get(sid) != message:
                    log.error("%s: read failed: %s", sid, message)
                    self._read_errors[sid] = message
                continue
            self._read_errors.pop(sid, None)
            if values.size:
                self.buffers[sid].append(values, t_end)
                self._pending[sid] = np.concatenate((self._pending[sid], values))
                self._pending_t_end[sid] = t_end

    def flush_display(self) -> None:
        dt_sample = 1.0 / self.rate_hz
        for sid, pending in self._pending.items():
            buckets = pending.size // self._bucket
            if buckets == 0:
                continue
            take = buckets * self._bucket
            block, rest = pending[:take], pending[take:]
            self._pending[sid] = rest
            values, bucket = decimate_minmax(block, 2 * buckets)
            dt_out = bucket * dt_sample / 2.0
            t_block_end = self._pending_t_end[sid] - rest.size * dt_sample
            self.hub.broadcast({
                "type": "samples",
                "sensor": sid,
                "t0": round(t_block_end - (values.size - 1) * dt_out, 4),
                "dt": dt_out,
                "unit": self.settings.sensor(sid).unit,
                "values": to_json_list(values, 3),
            })

    def _take_snapshots(self, now: float) -> None:
        """Keep the full-rate signal around each alert once it is complete."""
        while self._snapshot_due and self._snapshot_due[0][0] <= now:
            _, event = self._snapshot_due.pop(0)
            for sid in [event.sensor, *event.related_sensors]:
                if sid not in self.buffers:
                    continue
                t0, dt, values = self.buffers[sid].fast_range(event.t - SNAPSHOT_BEFORE_S,
                                                              event.t + SNAPSHOT_AFTER_S)
                if values.size:
                    snaps = self.snapshots[sid]
                    snaps.append((t0, dt, values.copy()))
                    del snaps[:-SNAPSHOTS_PER_SENSOR]

    def all_events(self, since: float | None = None, sensor: str | None = None) -> list[dict[str, Any]]:
        """Earlier incidents of the shift and live alerts, oldest first."""
        out = [e for e in [*self.history_events, *self.events]
               if (since is None or e.t >= since)
               and (sensor is None or e.sensor == sensor or sensor in e.related_sensors)]
        return [{**e.to_message(), "historic": e.id.startswith("H")} for e in out]

    def publish_event(self, event: Event) -> None:
        self._snapshot_due.append((event.t + SNAPSHOT_AFTER_S + 0.3, event))
        self.events.append(event)
        log.info("event %s %s: %s", event.kind, event.sensor, event.summary)
        self.hub.broadcast(event.to_message(), kinds=(COCKPIT, PHONE))

    # -- state for the UI --------------------------------------------------

    def sensor_state(self, sid: str) -> str:
        active = self.detector.active_kinds(sid)
        if "out_of_range" in active or ("drift" in active
                                        and self.detector.state[sid].drift_severity == "critical"):
            return "critical"
        return "warning" if active else "ok"

    def status_message(self) -> dict[str, Any]:
        sensors: dict[str, Any] = {}
        for cfg in self.settings.sensors:
            buf = self.buffers[cfg.id]
            _, _, last_s = buf.fast_window(1.0)
            ok = last_s[np.isfinite(last_s)]
            baseline = self.detector.baseline(cfg.id)
            metrics = self.detector.last_metrics.get(cfg.id)
            sensors[cfg.id] = {
                "latest": round_or_none(float(ok[-1]) if ok.size else None, 2),
                "mean_1s": round_or_none(float(ok.mean()) if ok.size else None, 2),
                "noise": round_or_none(baseline, 3),
                "noise_ratio": round_or_none(metrics.ratio_noise if metrics else None, 2),
                "state": self.sensor_state(cfg.id),
                "active": self.detector.active_kinds(cfg.id),
                "source": self.source_info.get(cfg.id, {}),
                "read_error": self._read_errors.get(cfg.id),
                "settings": dict(self.values[cfg.id]),
            }
        return {"type": "status", "t": time.time(), "uptime_s": round(time.time() - self.started_at),
                "clients": self.hub.counts(), "sensors": sensors}

    def recent_events(self, limit: int = 50, since: float | None = None,
                      sensor: str | None = None) -> list[dict[str, Any]]:
        selected = [e for e in self.events
                    if (since is None or e.t >= since)
                    and (sensor is None or e.sensor == sensor or sensor in e.related_sensors)]
        return [e.to_message() for e in selected[-limit:]]

    def last_event(self, kind: str, sensor: str | None = None) -> Event | None:
        for event in reversed(self.events):
            if event.kind == kind and (sensor is None or event.sensor == sensor
                                       or sensor in event.related_sensors):
                return event
        return None

    # -- helpers used by the API, the views and the AI tools ---------------

    def resolve_sensor(self, text: str | None) -> str:
        """Accept "s1", "1", "Sensor 1", "sensor one" or "Sensor eins"."""
        ids = [s.id for s in self.settings.sensors]
        if not text:
            return ids[0]
        raw = str(text).strip().lower()
        if raw in ids:
            return raw
        for cfg in self.settings.sensors:
            if raw == cfg.name.lower():
                return cfg.id
        words = re.findall(r"[a-zäöüß]+|\d+", raw)
        digits = [_NUMBER_WORDS.get(w, w) for w in words if w.isdigit() or w in _NUMBER_WORDS]
        for digit in digits:
            for cfg in self.settings.sensors:
                if cfg.id == f"s{digit}" or cfg.name.lower().endswith(f" {digit}"):
                    return cfg.id
        raise EngineError(f"unknown sensor {text!r}; known sensors: {', '.join(ids)}")

    def change_setting(self, sensor: str, name: str, value: Any) -> dict[str, Any]:
        sid = self.resolve_sensor(sensor)
        cfg = self.settings.sensor(sid)
        spec = cfg.settings.get(name)
        if spec is None:
            raise EngineError(f"{cfg.name} has no setting {name!r}; available: {', '.join(cfg.settings)}")
        try:
            new_value = spec.validate(value)
        except ValueError as exc:
            raise EngineError(str(exc)) from exc
        old_value = self.values[sid].get(name)
        self.values[sid][name] = new_value
        self.sources[sid].apply_setting(name, new_value)
        if name == "averaging":
            # noise level changes on purpose: learn the new normal from fresh data
            self.detector.relearn(sid, time.time())
        message = {"type": "setting_changed", "sensor": sid, "name": name,
                   "old": old_value, "new": new_value, "t": time.time()}
        self.hub.broadcast(message)
        log.info("%s: %s %s -> %s", sid, name, old_value, new_value)
        return {"ok": True, "sensor": sid, "name": name, "old": old_value, "new": new_value}

    def inject(self, kind: str, sensor: str | None = None, **params: Any) -> dict[str, Any]:
        if kind not in DEMO_KINDS:
            raise EngineError(f"unknown demo event {kind!r}; use one of {', '.join(DEMO_KINDS)}")
        now = time.time()
        if kind == "reset":
            for source in self.sources.values():
                if isinstance(source, SimulatedSensor):
                    source.inject("reset", now)
            for cfg in self.settings.sensors:  # back to the configured settings
                for name, spec in cfg.settings.items():
                    if self.values[cfg.id].get(name) != spec.default:
                        self.change_setting(cfg.id, name, spec.default)
            return {"ok": True, "kind": kind}
        sid = self.resolve_sensor(sensor)
        if kind == "misconfigure":
            # demo: someone left the sensor on a too-low averaging, so it is noisy
            return {"ok": True, "kind": kind,
                    **self.change_setting(sid, "averaging", params.get("value", 2))}
        try:
            if kind == "tap":
                fixture = self.settings.sensor(sid).fixture
                hit = []
                for cfg in self.settings.sensors:
                    if cfg.fixture != fixture:
                        continue
                    scale = 1.0 if cfg.id == sid else 0.8
                    params_i = dict(params)
                    params_i["amplitude"] = float(params.get("amplitude", 15.0)) * scale
                    # a knock travels through steel in microseconds: both sensors feel it at once
                    params_i["delay_s"] = 0.0
                    self.sources[cfg.id].inject("vibration", now, **params_i)
                    hit.append(cfg.id)
                return {"ok": True, "kind": kind, "sensors": hit}
            result = self.sources[sid].inject(kind, now, **params)
        except SourceError as exc:
            raise EngineError(str(exc)) from exc
        return {"ok": True, "kind": kind, "sensor": sid, **result}

    def noise_level(self, sid: str, seconds: float = 5.0) -> float | None:
        _, _, values = self.buffers[sid].fast_window(seconds)
        return robust_sigma(values)

    @staticmethod
    def finite_or_none(value: float | None) -> float | None:
        return None if value is None or not math.isfinite(value) else value
