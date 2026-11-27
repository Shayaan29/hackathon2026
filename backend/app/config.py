"""Configuration loading.

Two files hold everything a team is expected to change:

* ``backend/.env``                 secrets and runtime switches (API key, model, port, token)
* ``backend/config/sensors.yaml``  sensors, tolerances, dummy settings and alert thresholds
                                   (``sensors.live.yaml`` is the same for the real optoNCDT 5500)

The rest of the code reads the frozen :class:`Settings` object built here.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
FRONTEND_DIST = PROJECT_DIR / "frontend" / "dist"

load_dotenv(BACKEND_DIR / ".env", override=False)

SOURCE_TYPES = ("simulator", "replay", "medaqlib", "ild5500")
EVENT_KINDS = ("vibration", "noise", "drift", "out_of_range", "invalid")


class ConfigError(ValueError):
    """Raised when sensors.yaml or .env contains an invalid value."""


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}") from exc


_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def expand_env(value: Any, where: str) -> Any:
    """Replace ${NAME} and ${NAME:-default} in a text value by the entry in .env (or the default).

    This keeps addresses out of the sensor file: the file says ${LIVE_SENSOR_HOST:-169.254.168.150} and the
    laptop that runs the demo only has to set LIVE_SENSOR_HOST in backend/.env when the address differs."""
    if not isinstance(value, str) or "${" not in value:
        return value

    def fill(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        found = os.getenv(name)
        if found is not None and found.strip() != "":
            return found.strip()
        if default is not None:
            return default
        raise ConfigError(f"{where} uses ${{{name}}} but {name} is not set in backend/.env")

    return _VARIABLE.sub(fill, value)


@dataclass(frozen=True)
class Tolerance:
    low: float
    high: float

    def contains(self, value: float) -> bool:
        return self.low <= value <= self.high

    def as_dict(self) -> dict[str, float]:
        return {"low": self.low, "high": self.high}


@dataclass(frozen=True)
class SettingSpec:
    """One adjustable sensor setting (dummy until the real sensor is wired in)."""

    name: str
    default: Any
    minimum: float | None = None
    maximum: float | None = None
    options: tuple[Any, ...] | None = None
    description: str = ""

    def validate(self, value: Any) -> Any:
        """Return ``value`` coerced to the setting's type, or raise ValueError."""
        if self.options is not None:
            for option in self.options:
                if str(option).lower() == str(value).strip().lower():
                    return option
            allowed = ", ".join(str(o) for o in self.options)
            raise ValueError(f"{self.name} must be one of: {allowed}")
        if isinstance(self.default, (int, float)) and not isinstance(self.default, bool):
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{self.name} must be a number") from exc
            if self.minimum is not None and number < self.minimum:
                raise ValueError(f"{self.name} must be at least {self.minimum:g}")
            if self.maximum is not None and number > self.maximum:
                raise ValueError(f"{self.name} must be at most {self.maximum:g}")
            return int(round(number)) if isinstance(self.default, int) else number
        return str(value)

    def public(self) -> dict[str, Any]:
        data: dict[str, Any] = {"default": self.default, "description": self.description}
        if self.options is not None:
            data["options"] = list(self.options)
        if self.minimum is not None:
            data["min"] = self.minimum
        if self.maximum is not None:
            data["max"] = self.maximum
        return data


@dataclass(frozen=True)
class SensorConfig:
    id: str
    name: str
    model: str
    unit: str
    nominal: float
    tolerance: Tolerance
    fixture: str
    source: dict[str, Any]
    settings: dict[str, SettingSpec]

    @property
    def source_type(self) -> str:
        return str(self.source.get("type", "simulator"))

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "model": self.model,
            "unit": self.unit,
            "nominal": self.nominal,
            "tolerance": self.tolerance.as_dict(),
            "fixture": self.fixture,
            "source": self.source_type,
            "settings": {name: spec.public() for name, spec in self.settings.items()},
        }


@dataclass(frozen=True)
class DetectionConfig:
    eval_interval_s: float = 0.25
    baseline_s: float = 20.0
    vibration_window_s: float = 1.0
    vibration_ratio: float = 4.0
    # slower movements (a dent passing once per turn, runout) are not vibration
    vibration_min_hz: float = 12.0
    vibration_min_prominence: float = 6.0
    noise_window_s: float = 4.0
    noise_ratio: float = 2.5
    noise_hold_s: float = 2.0
    drift_window_s: float = 30.0
    drift_min_rate_per_min: float = 3.0
    drift_min_r2: float = 0.8
    drift_hold_s: float = 8.0
    drift_critical_minutes: float = 5.0
    invalid_window_s: float = 1.0
    invalid_min_share: float = 0.2
    out_of_range_window_s: float = 0.5
    rearm_s: float = 3.0
    cooldown_s: dict[str, float] = field(
        default_factory=lambda: {
            "vibration": 10.0,
            "noise": 30.0,
            "drift": 60.0,
            "out_of_range": 30.0,
            "invalid": 20.0,
        }
    )


@dataclass(frozen=True)
class EngineConfig:
    analysis_rate_hz: int = 500
    fast_buffer_s: int = 600
    slow_buffer_s: int = 28800
    backfill_s: int = 900
    part_rpm: float = 8.6  # how fast the measured part turns, for the twin and the runout


@dataclass(frozen=True)
class CockpitConfig:
    title: str = "KI Sensor Cockpit"
    team: str = "Team Name"
    assistant_name: str = "Epsilon"
    language: str = "en"


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    realtime_model: str
    realtime_voice: str
    transcribe_model: str
    cockpit_token: str
    host: str
    port: int
    source_override: str
    sensors_file: Path
    cockpit: CockpitConfig
    engine: EngineConfig
    detection: DetectionConfig
    sensors: tuple[SensorConfig, ...]
    # manuals and recommendations (all optional; the defaults work without a .env entry)
    text_model: str = ""
    embed_model: str = "text-embedding-3-small"
    knowledge_dir: Path = Path(__file__).resolve().parent.parent / "data" / "knowledge"
    recommend_prefetch: bool = True
    openai_base_url: str = "https://api.openai.com/v1"

    @property
    def voice_enabled(self) -> bool:
        return bool(self.openai_api_key)

    def sensor(self, sensor_id: str) -> SensorConfig:
        for sensor in self.sensors:
            if sensor.id == sensor_id:
                return sensor
        raise KeyError(sensor_id)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _as_float(value: Any, where: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{where} must be a number, got {value!r}") from exc


def _parse_setting(name: str, raw: Any) -> SettingSpec:
    if not isinstance(raw, dict):
        return SettingSpec(name=name, default=raw)
    options = raw.get("options")
    return SettingSpec(
        name=name,
        default=raw.get("value"),
        minimum=raw.get("min"),
        maximum=raw.get("max"),
        options=tuple(options) if options is not None else None,
        description=str(raw.get("description", "")),
    )


def _parse_sensor(raw: dict[str, Any], index: int, source_override: str) -> SensorConfig:
    where = f"sensors[{index}]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} must be a mapping")
    sensor_id = str(raw.get("id", "")).strip()
    if not sensor_id:
        raise ConfigError(f"{where}.id is required")
    tol_raw = raw.get("tolerance") or {}
    tolerance = Tolerance(
        low=_as_float(tol_raw.get("low", -50.0), f"{where}.tolerance.low"),
        high=_as_float(tol_raw.get("high", 50.0), f"{where}.tolerance.high"),
    )
    if tolerance.low >= tolerance.high:
        raise ConfigError(f"{where}.tolerance.low must be below tolerance.high")
    source = {key: expand_env(value, f"{where}.source.{key}")
              for key, value in dict(raw.get("source") or {"type": "simulator"}).items()}
    if source_override:
        source["type"] = source_override
    if source.get("type", "simulator") not in SOURCE_TYPES:
        raise ConfigError(f"{where}.source.type must be one of {', '.join(SOURCE_TYPES)}")
    settings_raw = raw.get("settings") or {}
    return SensorConfig(
        id=sensor_id,
        name=str(raw.get("name", sensor_id)),
        model=str(raw.get("model", "unknown model")),
        unit=str(raw.get("unit", "µm")),
        nominal=_as_float(raw.get("nominal", 0.0), f"{where}.nominal"),
        tolerance=tolerance,
        fixture=str(raw.get("fixture", "default")),
        source=source,
        settings={name: _parse_setting(name, value) for name, value in settings_raw.items()},
    )


def _parse_detection(raw: dict[str, Any] | None) -> DetectionConfig:
    raw = dict(raw or {})
    defaults = DetectionConfig()
    cooldown = dict(defaults.cooldown_s)
    cooldown.update({str(k): float(v) for k, v in (raw.pop("cooldown_s", None) or {}).items()})
    values: dict[str, Any] = {}
    for key, value in raw.items():
        if not hasattr(defaults, key):
            raise ConfigError(f"detection.{key} is not a known setting")
        values[key] = _as_float(value, f"detection.{key}")
    return DetectionConfig(cooldown_s=cooldown, **values)


def _parse_engine(raw: dict[str, Any] | None) -> EngineConfig:
    raw = raw or {}
    defaults = EngineConfig()
    values = {}
    for key in ("analysis_rate_hz", "fast_buffer_s", "slow_buffer_s", "backfill_s"):
        values[key] = int(raw.get(key, getattr(defaults, key)))
    if values["analysis_rate_hz"] < 50:
        raise ConfigError("engine.analysis_rate_hz must be at least 50")
    values["part_rpm"] = float(raw.get("part_rpm", defaults.part_rpm))
    return EngineConfig(**values)


def load_settings(sensors_file: str | Path | None = None) -> Settings:
    """Read .env and the sensor file into an immutable Settings object."""
    source_override = _env("SOURCE_OVERRIDE").lower()
    if source_override and source_override not in SOURCE_TYPES:
        raise ConfigError(f"SOURCE_OVERRIDE must be empty or one of {', '.join(SOURCE_TYPES)}")

    path = Path(sensors_file or _env("SENSORS_FILE", "config/sensors.yaml"))
    if not path.is_absolute():
        path = BACKEND_DIR / path
    if not path.exists():
        raise ConfigError(f"Sensor file not found: {path}")
    with path.open(encoding="utf-8") as handle:
        doc = yaml.safe_load(handle) or {}

    sensors_raw = doc.get("sensors") or []
    if not sensors_raw:
        raise ConfigError(f"{path.name} defines no sensors")
    sensors = tuple(_parse_sensor(raw, i, source_override) for i, raw in enumerate(sensors_raw))
    ids = [s.id for s in sensors]
    if len(ids) != len(set(ids)):
        raise ConfigError("sensor ids must be unique")

    cockpit_raw = doc.get("cockpit") or {}
    cockpit = CockpitConfig(
        title=str(cockpit_raw.get("title", CockpitConfig.title)),
        team=str(cockpit_raw.get("team", CockpitConfig.team)),
        assistant_name=str(cockpit_raw.get("assistant_name", CockpitConfig.assistant_name)),
        language=str(cockpit_raw.get("language", CockpitConfig.language)),
    )

    return Settings(
        openai_api_key=_env("OPENAI_API_KEY"),
        realtime_model=_env("OPENAI_REALTIME_MODEL", "gpt-realtime-mini"),
        realtime_voice=_env("OPENAI_REALTIME_VOICE", "marin"),
        transcribe_model=_env("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe"),
        cockpit_token=_env("COCKPIT_TOKEN"),
        host=_env("HOST", "0.0.0.0"),
        port=_env_int("PORT", 8000),
        source_override=source_override,
        sensors_file=path,
        cockpit=cockpit,
        engine=_parse_engine(doc.get("engine")),
        detection=_parse_detection(doc.get("detection")),
        sensors=sensors,
        text_model=_env("OPENAI_TEXT_MODEL"),
        embed_model=_env("OPENAI_EMBED_MODEL", "text-embedding-3-small"),
        knowledge_dir=Path(_env("KNOWLEDGE_DIR", str(Path(__file__).resolve().parent.parent / "data" / "knowledge"))),
        recommend_prefetch=_env("RECOMMEND_PREFETCH", "1").lower() not in ("0", "false", "no", "off"),
        openai_base_url=_env("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    )
