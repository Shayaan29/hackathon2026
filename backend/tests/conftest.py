from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from app.buffer import SensorBuffer  # noqa: E402
from app.config import Settings, load_settings  # noqa: E402
from app.detection import Detector, Event  # noqa: E402
from app.sources import SimulatedSensor  # noqa: E402

T0 = 1_700_000_000.0


@pytest.fixture()
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("COCKPIT_TOKEN", "test-token")
    monkeypatch.setenv("SOURCE_OVERRIDE", "")
    return load_settings(BACKEND / "config" / "sensors.yaml")


class Rig:
    """Simulated sensors, buffers and the detector on a manual clock."""

    def __init__(self, settings: Settings, history_s: float = 60.0) -> None:
        self.settings = settings
        self.rate = settings.engine.analysis_rate_hz
        self.sources = {c.id: SimulatedSensor(c, self.rate) for c in settings.sensors}
        self.buffers = {c.id: SensorBuffer(self.rate, 600, 3600) for c in settings.sensors}
        for sid, src in self.sources.items():
            values, t_end = src.backfill(history_s, T0)
            self.buffers[sid].append(values, t_end)
        self.detector = Detector(settings.sensors, settings.detection, self.rate)
        self.t = T0
        self.events: list[Event] = []

    def run(self, seconds: float) -> list[Event]:
        new: list[Event] = []
        end = self.t + seconds
        last_eval = self.t
        while self.t < end:
            self.t += 0.05
            for sid, src in self.sources.items():
                values, t_end = src.read(self.t)
                self.buffers[sid].append(values, t_end)
            if self.t - last_eval >= 0.25:
                last_eval = self.t
                new.extend(self.detector.evaluate(self.buffers, self.t))
        self.events.extend(new)
        return new


@pytest.fixture()
def rig(settings: Settings) -> Rig:
    return Rig(settings)
