"""Data sources: the optoNCDT 5500 over Ethernet, and any sensor MEDAQLib supports."""

from __future__ import annotations

from typing import Callable

from ..config import SOURCE_TYPES, SensorConfig
from .base import SensorSource, SourceError, UnavailableSource
from .ild5500 import Ild5500Source
from .medaqlib import MedaqlibSource

__all__ = ["SensorSource", "SourceError", "UnavailableSource", "create_source", "register_source",
           "MedaqlibSource", "Ild5500Source"]

_FACTORIES: dict[str, Callable[[SensorConfig, int], SensorSource]] = {
    "ild5500": Ild5500Source,
    "medaqlib": MedaqlibSource,
}


def register_source(kind: str, factory: Callable[[SensorConfig, int], SensorSource]) -> None:
    """Add a source type. The tests add their simulated sensor this way; the cockpit itself has none."""
    _FACTORIES[kind] = factory
    SOURCE_TYPES.add(kind)


def create_source(cfg: SensorConfig, rate_hz: int) -> SensorSource:
    factory = _FACTORIES.get(cfg.source_type)
    if factory is None:
        raise SourceError(f"unknown source type {cfg.source_type!r}")
    return factory(cfg, rate_hz)
