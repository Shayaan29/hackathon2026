"""Part check: teach the cockpit one kind of part, and it says when something else lies under the sensor.

A laser sensor measures a distance, so a part is recognised by where its top surface is. The operator puts a good
part under the sensor, holds it still and presses "Teach this part". The cockpit remembers the position and
accepts a band around it (the *margin*, 0.10 mm unless changed). Pressing it again with another good part
widens the band to cover both, so the band follows the real spread of the parts instead of a guess.

Optionally the operator also teaches the empty plate. Then taking a part away is "nothing under the sensor"
and not an alert, and the cockpit can tell the height of a part (empty plate minus part).

Rules of thumb that keep it calm:

* Only a *steady* reading counts: at least 80 % valid and a spread of the last 0.4 s below 40 µm. A hand
  sliding a part in, or a part that is being swapped, is "waiting for a steady reading", never an alert.
* A new state has to hold for 0.6 s before it is believed.
* One alert per foreign part. A second alert follows only after a good part or the empty plate was seen in
  between, or when the target settles clearly somewhere else.

Positions are micrometres from the start of the measuring range, like everywhere in the cockpit. The sensor looks
at the top surface, so a part that is the same height but another shape or colour looks the same to it.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .buffer import SensorBuffer
from .config import SensorConfig
from .detection import Event

log = logging.getLogger("cockpit.partcheck")

KIND = "wrong_part"
WINDOW_S = 0.4              # the stretch of signal that must be steady
LEARN_WINDOW_S = 1.0        # the stretch that teaching averages over
MIN_VALID_SHARE = 0.8
STEADY_SPREAD_UM = 40.0     # 5th to 95th percentile of the window
CONFIRM_S = 0.6             # a state must hold this long before it counts
MIN_ALERT_GAP_S = 1.5
DEFAULT_MARGIN_UM = 100.0
MARGIN_RANGE_UM = (10.0, 5000.0)
EMPTY_BAND_UM = 150.0       # how close to the taught empty plate still counts as empty
MIN_SAMPLES = 5


class PartCheckError(ValueError):
    """Teaching was not possible; the message says why in plain words."""


@dataclass
class Reference:
    samples_um: list[float]
    margin_um: float = DEFAULT_MARGIN_UM
    taught_at: float = 0.0

    @property
    def low_um(self) -> float:
        return min(self.samples_um)

    @property
    def high_um(self) -> float:
        return max(self.samples_um)

    @property
    def center_um(self) -> float:
        return (self.low_um + self.high_um) / 2.0

    @property
    def half_width_um(self) -> float:
        return (self.high_um - self.low_um) / 2.0 + self.margin_um


@dataclass
class Reading:
    position_um: float | None
    spread_um: float
    valid_share: float

    @property
    def steady(self) -> bool:
        return (self.position_um is not None and self.valid_share >= MIN_VALID_SHARE
                and self.spread_um <= STEADY_SPREAD_UM)


@dataclass
class _Track:
    reference: Reference | None = None
    empty_um: float | None = None
    state: str = "waiting"          # no_reference | waiting | match | different | empty
    since: float = 0.0
    pending: str | None = None
    pending_since: float = 0.0
    latched_um: float | None = None
    last_alert: float = -math.inf
    reading: Reading | None = None
    alerts: int = 0


def measure(buf: SensorBuffer, seconds: float) -> Reading:
    """Position (median) and spread of the last ``seconds`` of valid readings."""
    _, _, values = buf.fast_window(seconds)
    if values.size == 0:
        return Reading(None, 0.0, 0.0)
    finite = values[np.isfinite(values)]
    share = finite.size / values.size
    if finite.size < MIN_SAMPLES:
        return Reading(None, 0.0, share)
    low, high = np.percentile(finite, [5, 95])
    return Reading(float(np.median(finite)), float(high - low), share)


class PartCheck:
    def __init__(self, sensors: tuple[SensorConfig, ...], path: Path | None = None) -> None:
        self.sensors = {s.id: s for s in sensors}
        self.path = path
        self.tracks = {sid: _Track() for sid in self.sensors}
        self._lock = threading.RLock()
        self._ids = 0
        self._load()

    # -- persistence -------------------------------------------------------

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
            for sid, item in doc.items():
                track = self.tracks.get(sid)
                if track is None or not isinstance(item, dict):
                    continue
                samples = [float(x) for x in item.get("samples_um", []) if math.isfinite(float(x))]
                if samples:
                    margin = min(max(float(item.get("margin_um", DEFAULT_MARGIN_UM)), MARGIN_RANGE_UM[0]),
                                 MARGIN_RANGE_UM[1])
                    track.reference = Reference(samples, margin, float(item.get("taught_at", 0.0)))
                empty = item.get("empty_um")
                if empty is not None and math.isfinite(float(empty)):
                    track.empty_um = float(empty)
        except (OSError, ValueError, TypeError) as exc:
            log.warning("could not read the taught parts from %s: %s", self.path, exc)

    def _save(self) -> None:
        if self.path is None:
            return
        doc = {sid: {"samples_um": t.reference.samples_um if t.reference else [],
                     "margin_um": t.reference.margin_um if t.reference else DEFAULT_MARGIN_UM,
                     "taught_at": t.reference.taught_at if t.reference else 0.0,
                     "empty_um": t.empty_um}
               for sid, t in self.tracks.items() if t.reference or t.empty_um is not None}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
        except OSError as exc:
            log.warning("could not save the taught parts to %s: %s", self.path, exc)

    # -- state -------------------------------------------------------------

    def reset(self) -> None:
        """Forget what was seen (not what was taught)."""
        with self._lock:
            for track in self.tracks.values():
                track.state, track.pending, track.latched_um, track.reading = "waiting", None, None, None

    def supported(self, sid: str) -> bool:
        cfg = self.sensors.get(sid)
        return cfg is not None and cfg.unit == "µm"

    def is_different(self, sid: str) -> bool:
        track = self.tracks.get(sid)
        return bool(track and track.state == "different")

    @staticmethod
    def empty_band(track: _Track) -> float:
        """How close to the taught empty plate a reading must be to count as the empty plate."""
        return max(EMPTY_BAND_UM, track.reference.margin_um if track.reference else 0.0)

    def classify(self, track: _Track, position: float) -> str:
        ref = track.reference
        if ref is not None and abs(position - ref.center_um) <= ref.half_width_um:
            return "match"
        if track.empty_um is not None and abs(position - track.empty_um) <= self.empty_band(track):
            return "empty"
        return "different" if ref is not None else "waiting"

    # -- teaching ----------------------------------------------------------

    def learn(self, sid: str, buf: SensorBuffer, kind: str = "part", add: bool = False,
              margin_um: float | None = None, now: float | None = None) -> dict[str, Any]:
        """Teach what lies under the sensor right now: ``part`` (a good part) or ``empty`` (nothing)."""
        if not self.supported(sid):
            raise PartCheckError("The part check works on sensors that report micrometres.")
        if kind not in ("part", "empty"):
            raise PartCheckError("Teach either the part or the empty plate.")
        reading = measure(buf, LEARN_WINDOW_S)
        if reading.position_um is None or reading.valid_share < MIN_VALID_SHARE:
            raise PartCheckError("The sensor has no valid reading. Put the part in the laser spot and try again.")
        if reading.spread_um > STEADY_SPREAD_UM:
            raise PartCheckError("The reading is not steady yet. Hold the part still under the sensor for a "
                                 "second and press the button again.")
        now = time.time() if now is None else now
        with self._lock:
            track = self.tracks[sid]
            if kind == "empty":
                track.empty_um = reading.position_um
                track.state, track.since, track.pending = "empty", now, None
            else:
                margin = self._clean_margin(margin_um, track)
                if add and track.reference is not None:
                    track.reference.samples_um.append(reading.position_um)
                    track.reference.margin_um = margin
                else:
                    track.reference = Reference([reading.position_um], margin, now)
                track.reference.taught_at = now
                track.state, track.since, track.pending, track.latched_um = "match", now, None, None
            self._save()
        return self.public(sid)

    def set_margin(self, sid: str, margin_um: float) -> dict[str, Any]:
        with self._lock:
            track = self.tracks[sid]
            if track.reference is None:
                raise PartCheckError("Teach a part first, then change the allowed deviation.")
            track.reference.margin_um = self._clean_margin(margin_um, track)
            self._save()
        return self.public(sid)

    @staticmethod
    def _clean_margin(margin_um: float | None, track: _Track) -> float:
        if margin_um is None:
            return track.reference.margin_um if track.reference else DEFAULT_MARGIN_UM
        if not math.isfinite(margin_um):
            raise PartCheckError("The allowed deviation must be a number.")
        return min(max(float(margin_um), MARGIN_RANGE_UM[0]), MARGIN_RANGE_UM[1])

    def forget(self, sid: str, what: str = "all") -> dict[str, Any]:
        with self._lock:
            track = self.tracks[sid]
            if what in ("all", "empty"):
                track.empty_um = None
            if what in ("all", "part"):
                track.reference = None
            track.state, track.pending, track.latched_um = "waiting", None, None
            self._save()
        return self.public(sid)

    # -- watching ----------------------------------------------------------

    def evaluate(self, buffers: dict[str, SensorBuffer], now: float) -> list[Event]:
        events: list[Event] = []
        with self._lock:
            for sid, track in self.tracks.items():
                buf = buffers.get(sid)
                if buf is None or not self.supported(sid):
                    continue
                if track.reference is None:
                    track.state, track.reading = "no_reference", None
                    continue
                track.reading = measure(buf, WINDOW_S)
                event = self._step(sid, track, track.reading, now)
                if event is not None:
                    events.append(event)
        return events

    def _step(self, sid: str, track: _Track, reading: Reading, now: float) -> Event | None:
        if track.state == "no_reference":
            track.state, track.since = "waiting", now
        if not reading.steady:
            track.pending = None            # moving or no target: keep believing the last state, but wait
            return None
        position = reading.position_um
        assert position is not None
        found = self.classify(track, position)
        if found == track.state and not self._moved_elsewhere(track, found, position):
            track.pending = None
            return None
        if track.pending != found:
            track.pending, track.pending_since = found, now
            return None
        if now - track.pending_since < CONFIRM_S:
            return None
        previous = track.state
        track.state, track.since, track.pending = found, now, None
        if found != "different":
            track.latched_um = None
            return None
        track.latched_um = position
        if now - track.last_alert < MIN_ALERT_GAP_S:
            return None
        track.last_alert = now
        track.alerts += 1
        return self._event(sid, track, position, now, previous)

    def _moved_elsewhere(self, track: _Track, found: str, position: float) -> bool:
        """Still a foreign part, but clearly another one than the one already reported."""
        if found != "different" or track.latched_um is None or track.reference is None:
            return False
        return abs(position - track.latched_um) > max(track.reference.half_width_um, EMPTY_BAND_UM)

    def _event(self, sid: str, track: _Track, position: float, now: float, previous: str) -> Event:
        cfg = self.sensors[sid]
        ref = track.reference
        assert ref is not None
        deviation = position - ref.center_um
        numbers: dict[str, Any] = {
            "value": round(position, 1), "reference": round(ref.center_um, 1),
            "half_width": round(ref.half_width_um, 1), "deviation": round(deviation, 1),
            "parts": len(ref.samples_um), "unit": cfg.unit,
        }
        side = "farther from the sensor" if deviation > 0 else "closer to the sensor"
        text = (f"Different part detected on {cfg.name}: it reads {cfg.pos(position, signed=False)}, but the taught "
                f"part reads {cfg.pos(ref.center_um, signed=False)} ± {cfg.pos(ref.half_width_um, signed=False)}. "
                f"Its top surface is {cfg.pos(abs(deviation), signed=False)} {side}.")
        if track.empty_um is not None:
            height, ref_height = track.empty_um - position, track.empty_um - ref.center_um
            numbers.update({"height": round(height, 1), "reference_height": round(ref_height, 1)})
            if height > 0 and ref_height > 0:
                text += (f" That is {cfg.pos(height, signed=False)} above the empty plate, the taught part is "
                         f"{cfg.pos(ref_height, signed=False)}.")
                if abs(height - 2.0 * ref_height) <= max(0.1 * ref_height, ref.half_width_um):
                    numbers["stacked"] = True
                    text += " That is about twice the taught part: two parts may lie on top of each other."
        self._ids += 1
        return Event(id=f"ev-{int(now) % 100000}-p{self._ids}", sensor=sid, kind=KIND, severity="warning", t=now,
                     numbers=numbers, summary=text)

    # -- what the screen shows ---------------------------------------------

    def public(self, sid: str) -> dict[str, Any]:
        track = self.tracks.get(sid)
        if track is None or not self.supported(sid):
            return {"supported": False, "state": "no_reference", "steady": False, "position_um": None,
                    "message": "The part check needs a sensor that reports micrometres.", "reference": None,
                    "empty_um": None, "height_um": None, "alerts": 0}
        cfg = self.sensors[sid]
        ref = track.reference
        reading = track.reading
        steady = bool(reading and reading.steady)
        position = reading.position_um if reading else None
        height = (track.empty_um - ref.center_um
                  if ref and track.empty_um is not None and track.empty_um - ref.center_um > 0 else None)
        return {
            "supported": True, "state": track.state if ref is not None else "no_reference", "steady": steady,
            "position_um": None if position is None else round(position, 1),
            "message": self._message(cfg, track, steady, position),
            "reference": None if ref is None else {
                "center_um": round(ref.center_um, 1), "half_width_um": round(ref.half_width_um, 1),
                "margin_um": round(ref.margin_um, 1), "parts": len(ref.samples_um),
                "low_um": round(ref.low_um, 1), "high_um": round(ref.high_um, 1),
                "taught_at": ref.taught_at},
            "empty_um": None if track.empty_um is None else round(track.empty_um, 1),
            "height_um": None if height is None else round(height, 1),
            "alerts": track.alerts,
        }

    def _message(self, cfg: SensorConfig, track: _Track, steady: bool, position: float | None) -> str:
        ref = track.reference
        if ref is None:
            return ("Nothing is taught yet. Put a good part under the sensor, hold it still and press "
                    "Teach this part.")
        band = f"{cfg.pos(ref.center_um, signed=False)} ± {cfg.pos(ref.half_width_um, signed=False)}"
        here = None if position is None else cfg.pos(position, signed=False)
        if steady and here and track.pending is not None and track.pending != track.state:
            # the reading changed a moment ago: say so instead of repeating the old verdict next to the new number
            return f"Checking: it now reads {here}. The taught part reads {band}."
        if track.state == "match":
            text = (f"This is the taught part: it reads {here} (taught {band})." if here
                    else f"This is the taught part (taught {band}).")
        elif track.state == "different":
            text = f"Different part: it reads {here}, the taught part reads {band}." if here else "Different part."
        elif track.state == "empty":
            text = "Nothing under the sensor (the empty plate)."
        else:
            text = f"Waiting for a part. The taught part reads {band}."
        return text if steady or track.state == "waiting" else "Waiting for a steady reading. " + text
