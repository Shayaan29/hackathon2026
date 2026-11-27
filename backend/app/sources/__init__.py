"""Data sources: simulator (dummy data), CSV replay, live Ethernet (optoNCDT 5500) and live MEDAQLib."""

from __future__ import annotations

from ..config import SensorConfig
from .base import SensorSource, SourceError
from .ild5500 import Ild5500Source
from .medaqlib import MedaqlibSource
from .replay import CsvReplaySource
from .simulator import SimulatedSensor

__all__ = ["SensorSource", "SourceError", "create_source", "SimulatedSensor",
           "CsvReplaySource", "MedaqlibSource", "Ild5500Source"]


def create_source(cfg: SensorConfig, rate_hz: int) -> SensorSource:
    kind = cfg.source_type
    if kind == "simulator":
        return SimulatedSensor(cfg, rate_hz)
    if kind == "replay":
        return CsvReplaySource(cfg, rate_hz)
    if kind == "medaqlib":
        return MedaqlibSource(cfg, rate_hz)
    if kind == "ild5500":
        return Ild5500Source(cfg, rate_hz)
    raise SourceError(f"unknown source type {kind!r}")
