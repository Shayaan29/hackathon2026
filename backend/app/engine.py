"""The acquisition loop: sources -> buffers -> alert rules -> WebSocket clients.

Runs inside the FastAPI event loop. Every tick it pulls new samples from each
source, stores them, streams a peak-preserving thinned copy to the cockpit,
runs the alert rules, the part check and the quality station, and publishes
status, events and every finished part.
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

from . import sensor_specs
from .analysis import decimate_minmax, robust_sigma, round_or_none, to_json_list
from .buffer import SensorBuffer
from .config import Settings
from .detection import Detector, Event
from .hub import COCKPIT, PHONE, Hub
from .partcheck import PartCheck
from .sources import SensorSource, UnavailableSource, create_source
from .station import Station

log = logging.getLogger("cockpit.engine")

_NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                 "eins": "1", "zwei": "2", "drei": "3", "vier": "4", "fünf": "5"}


class EngineError(ValueError):
    """A request the engine cannot fulfil, such as an unknown sensor."""


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
        # the taught part of each sensor lives next to the manuals, in backend/data
        self.partcheck = PartCheck(settings.sensors, settings.knowledge_dir.parent / "part_reference.json")
        # every part that rested under a sensor, kept in backend/data as well
        self.station = Station(settings.sensors, self.partcheck, settings.knowledge_dir.parent / "part_log.jsonl",
                               on_part=self._announce_part)
        self.events: deque[Event] = deque(maxlen=300)
        self._pending = {s.id: np.empty(0) for s in settings.sensors}
        self._pending_t_end: dict[str, float] = {}
        self._bucket = max(1, int(round(self.rate_hz / (self.display_points_per_s / 2))))
        self._read_errors: dict[str, str] = {}
        self._task: asyncio.Task[None] | None = None
        self.started_at = time.time()

    # -- lifecycle ---------------------------------------------------------

    def start_sources(self) -> None:
        now = time.time()
        for cfg in self.settings.sensors:
            info: dict[str, Any] = {"type": cfg.source_type, "error": None}
            try:
                source: SensorSource = create_source(cfg, self.rate_hz)
                source.start()
            except Exception as exc:  # noqa: BLE001 - shown in the cockpit instead of numbers
                log.error("%s: the %s source could not start: %s", cfg.id, cfg.source_type, exc)
                source = UnavailableSource(cfg, self.rate_hz, f"Cannot start the sensor connection: {exc}")
                info["error"] = str(exc)
            self.sources[cfg.id] = source
            self.source_info[cfg.id] = info
        for cfg in self.settings.sensors:
            history = self.sources[cfg.id].backfill(self.settings.engine.backfill_s, now)
            if history is not None:
                values, t_end = history
                self.buffers[cfg.id].append(values, t_end)
            log.info("%s: %s source ready", cfg.id, self.source_info[cfg.id]["type"])

    def _stop_sources(self) -> None:
        for source in self.sources.values():
            try:
                source.stop()
            except Exception:  # noqa: BLE001
                log.exception("error while stopping a source")

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
                for event in self.partcheck.evaluate(self.buffers, now):
                    self.publish_event(event)
                for event in self.station.evaluate(self.buffers, now):
                    self.publish_event(event)
                last_eval = now
            if now - last_status >= self.status_interval_s:
                self.hub.broadcast(self.status_message())
                last_status = now
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

    def _enrich(self, event: Event) -> None:
        """Add what only the live source knows to an alert: why the readings were invalid."""
        if event.kind != "invalid":
            return
        reasons = (self._live_status(event.sensor) or {}).get("invalid_reasons")
        if not reasons:
            return
        cfg = self.settings.sensor(event.sensor)
        main = sensor_specs.main_reason(reasons)
        event.numbers["reasons"] = reasons
        event.numbers["main_reason"] = main
        event.summary = (f"{cfg.name} reports invalid readings: {event.numbers.get('invalid_percent')}% of the last "
                         f"second, {sensor_specs.reasons_text(reasons)}. "
                         f"{sensor_specs.invalid_hint(main, cfg.spec)}")

    def publish_event(self, event: Event) -> None:
        self._enrich(event)
        self.events.append(event)
        log.info("event %s %s: %s", event.kind, event.sensor, event.summary)
        self.hub.broadcast(event.to_message(), kinds=(COCKPIT, PHONE))

    def _announce_part(self, sid: str, part: dict[str, Any]) -> None:
        """A part left the sensor: the cockpit and the phones add it to their part lists."""
        self.hub.broadcast({"type": "part", "sensor": sid, "part": part,
                            "station": self.station.public(sid, time.time())}, kinds=(COCKPIT, PHONE))

    # -- state for the UI --------------------------------------------------

    def active_kinds(self, sid: str) -> list[str]:
        """Every alert that is on for a sensor: the signal rules, the part check and the quality station."""
        kinds = self.detector.active_kinds(sid)
        if self.partcheck.is_different(sid):
            kinds = [*kinds, "wrong_part"]
        return [*kinds, *self.station.active_kinds(sid)]

    def sensor_state(self, sid: str) -> str:
        active = self.active_kinds(sid)
        if "out_of_range" in active:
            return "critical"
        return "warning" if active else "ok"

    def _live_status(self, sid: str) -> dict[str, Any] | None:
        """Connection state of the sensor (address, rate, what is wrong)."""
        source = self.sources.get(sid)
        if source is None:
            return None
        try:
            return source.status() or None
        except Exception:  # noqa: BLE001 - a status must never break the status message
            log.exception("%s: could not read the live status", sid)
            return None

    def status_message(self) -> dict[str, Any]:
        sensors: dict[str, Any] = {}
        for cfg in self.settings.sensors:
            buf = self.buffers[cfg.id]
            _, _, last_s = buf.fast_window(1.0)
            ok = last_s[np.isfinite(last_s)]
            baseline = self.detector.baseline(cfg.id)
            sensors[cfg.id] = {
                "latest": round_or_none(float(ok[-1]) if ok.size else None, 2),
                "mean_1s": round_or_none(float(ok.mean()) if ok.size else None, 2),
                "noise": round_or_none(baseline, 3),
                "state": self.sensor_state(cfg.id),
                "active": self.active_kinds(cfg.id),
                "part": self.partcheck.public(cfg.id),
                "station": self.station.public(cfg.id, time.time()),
                "source": self.source_info.get(cfg.id, {}),
                "live": self._live_status(cfg.id),
                "read_error": self._read_errors.get(cfg.id),
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

    def noise_level(self, sid: str, seconds: float = 5.0) -> float | None:
        _, _, values = self.buffers[sid].fast_window(seconds)
        return robust_sigma(values)

    @staticmethod
    def finite_or_none(value: float | None) -> float | None:
        return None if value is None or not math.isfinite(value) else value
