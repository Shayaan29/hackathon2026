"""Read a CSV recording of any shape.

A file from sensorTOOL or a spreadsheet can use ";", "," or tabs, decimal commas
or decimal points, metadata lines above the table, a time column or none, and
units in the header or nowhere. This module works those out from the file,
reports what it found, and loads the chosen columns onto one uniform time grid
at the analysis rate. Whatever it cannot decide is a field the user corrects in
the mapping dialog. Nothing is invented: a missing time column is never guessed,
and an unknown unit blocks the analysis until someone picks one.
"""

from __future__ import annotations

import io
import itertools
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

from .config import SensorConfig, Tolerance

MICRO = "µm"  # the unit the cockpit works in
MAX_ROWS = 20_000_000
MAX_BYTES = 250 * 1024 * 1024
MIN_ROWS = 5
INVALID_TEXT = frozenset({"", "invalid", "nan", "inf", "-inf", "+inf", "n/a", "na", "---", "--", "-",
                          "err", "error", "none", "null"})
TIME_NAME = re.compile(r"zeit|time|sek|second|timestamp|^\s*t\s*$|^\s*t\s*[\[(]", re.IGNORECASE)
INDEX_NAME = re.compile(r"^\s*(index|nr\.?|no\.?|#|sample|probe|row|count)\s*$", re.IGNORECASE)
UNIT_RE = re.compile(r"[\[(]\s*([^\])]{1,12}?)\s*[\])]")
LENGTH_UNITS = {MICRO: 1.0, "mm": 1000.0, "m": 1_000_000.0, "nm": 0.001}
UNIT_ALIASES = {"um": MICRO, "μm": MICRO, MICRO: MICRO, "micron": MICRO, "microns": MICRO,
                "mm": "mm", "m": "m", "nm": "nm"}
TIME_UNIT_FACTOR = {"s": 1.0, "sec": 1.0, "ms": 1e-3, "min": 60.0, "h": 3600.0}


class IngestError(ValueError):
    """The file cannot be read as a recording. The message says what to change."""


@dataclass
class SensorColumn:
    index: int
    name: str
    unit: str = MICRO
    include: bool = True
    scale: float = 1.0
    zero: str = "start"  # "start": centre on the median of the first 10 s, "none": keep as recorded
    fixture: str = "recording"
    tolerance_low: float = -40.0
    tolerance_high: float = 40.0
    tolerance_source: str = "placeholder"  # until someone types the real band


@dataclass
class Mapping:
    delimiter: str
    decimal: str
    data_line: int
    time_index: int | None
    time_unit: str
    rate_hz: float | None
    part_rpm: float
    sensors: list[SensorColumn]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "delimiter": self.delimiter, "decimal": self.decimal, "data_line": self.data_line,
            "time_index": self.time_index, "time_unit": self.time_unit, "rate_hz": self.rate_hz,
            "part_rpm": self.part_rpm,
            "sensors": [{"index": s.index, "name": s.name, "unit": s.unit, "include": s.include,
                         "scale": s.scale, "zero": s.zero, "fixture": s.fixture,
                         "tolerance_low": s.tolerance_low, "tolerance_high": s.tolerance_high,
                         "tolerance_source": s.tolerance_source} for s in self.sensors],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Mapping":
        """Validate a mapping sent by the browser. Raises IngestError with a plain message."""
        try:
            delimiter = str(data.get("delimiter", ";"))
            decimal = str(data.get("decimal", "."))
            if delimiter not in (";", ",", "\t"):
                raise IngestError("The column separator must be ';', ',' or a tab.")
            if decimal not in (".", ","):
                raise IngestError("The decimal sign must be '.' or ','.")
            if delimiter == "," and decimal == ",":
                raise IngestError("A comma cannot be both the column separator and the decimal sign.")
            time_raw = data.get("time_index")
            time_index = None if time_raw in (None, "", -1) else int(time_raw)
            time_unit = str(data.get("time_unit") or "s")
            if time_unit not in TIME_UNIT_FACTOR:
                raise IngestError(f"The time unit must be one of {', '.join(TIME_UNIT_FACTOR)}.")
            rate_raw = data.get("rate_hz")
            rate = None if rate_raw in (None, "") else float(rate_raw)
            if time_index is None and (rate is None or rate <= 0):
                raise IngestError("This file has no time column. Enter its sample rate in Hz.")
            sensors: list[SensorColumn] = []
            for raw in data.get("sensors", []):
                unit = normalize_unit(raw.get("unit"))
                if unit not in LENGTH_UNITS:
                    raise IngestError(f"Unit '{unit}' is not a length unit. Choose µm, mm, m or nm, "
                                      "or leave the column out.")
                scale_raw = raw.get("scale")
                scale = LENGTH_UNITS[unit] if scale_raw in (None, "") else float(scale_raw)
                column = SensorColumn(
                    index=int(raw["index"]),
                    name=str(raw.get("name") or f"Sensor {len(sensors) + 1}")[:40],
                    unit=unit, include=bool(raw.get("include", True)), scale=scale,
                    zero=str(raw.get("zero", "start")),
                    fixture=str(raw.get("fixture") or "recording")[:40],
                    tolerance_low=float(raw.get("tolerance_low", -40.0)),
                    tolerance_high=float(raw.get("tolerance_high", 40.0)),
                    tolerance_source=str(raw.get("tolerance_source", "placeholder")),
                )
                if column.tolerance_low >= column.tolerance_high:
                    raise IngestError(f"{column.name}: the lower tolerance limit must be below the upper one.")
                if column.scale == 0 or not math.isfinite(column.scale):
                    raise IngestError(f"{column.name}: the scale must be a number other than zero.")
                if column.zero not in ("start", "none"):
                    raise IngestError(f"{column.name}: zero must be 'start' or 'none'.")
                sensors.append(column)
            part_rpm = max(0.0, float(data.get("part_rpm") or 0.0))
            data_line = max(0, int(data.get("data_line", 0)))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise IngestError(f"The mapping is not valid: {exc}") from exc
        included = [s for s in sensors if s.include]
        if not included:
            raise IngestError("Select at least one sensor column.")
        if len({s.index for s in included}) != len(included):
            raise IngestError("Each sensor column can only be used once.")
        if time_index is not None and time_index in {s.index for s in included}:
            raise IngestError("The time column cannot also be a sensor.")
        return cls(delimiter=delimiter, decimal=decimal, data_line=data_line, time_index=time_index,
                   time_unit=time_unit, rate_hz=rate, part_rpm=part_rpm, sensors=sensors)


@dataclass
class Layout:
    encoding: str
    delimiter: str
    decimal: str
    data_line: int
    header: list[str] | None
    column_count: int
    numeric_share: list[float]
    time_index: int | None
    time_unit: str
    time_unit_guessed: bool
    rate_hz: float | None
    index_columns: list[int]
    data_rows: int
    warnings: list[str] = field(default_factory=list)


@dataclass
class Recording:
    """A loaded file on one uniform time grid, values in µm around the reference."""

    name: str
    rate_hz: int
    duration_s: float
    source_rate_hz: float | None
    rows: int
    sensors: list[dict[str, Any]]
    values: dict[str, np.ndarray]
    warnings: list[str]
    metadata: list[str] = field(default_factory=list)  # the file's own header lines, verbatim

    def sensor_configs(self) -> tuple[SensorConfig, ...]:
        return tuple(
            SensorConfig(id=s["id"], name=s["name"], model="recording", unit=MICRO, nominal=0.0,
                         tolerance=Tolerance(s["tolerance"]["low"], s["tolerance"]["high"]),
                         fixture=s["fixture"], source={"type": "recording"}, settings={})
            for s in self.sensors)

    def summary(self) -> dict[str, Any]:
        return {"name": self.name, "rate_hz": self.rate_hz, "source_rate_hz": self.source_rate_hz,
                "duration_s": self.duration_s, "rows": self.rows, "sensors": self.sensors,
                "warnings": self.warnings}


# -- decoding and layout -----------------------------------------------------

def decode(raw: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), "latin-1"


def normalize_unit(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return MICRO
    return UNIT_ALIASES.get(text.lower(), UNIT_ALIASES.get(text, text))


def parse_number(cell: str, decimal: str = ".") -> float:
    """A cell as a float. Empty cells and words such as 'invalid' become NaN."""
    text = cell.strip().strip('"').strip()
    if text.lower() in INVALID_TEXT:
        return math.nan
    if decimal == ",":
        if "." in text and "," in text:
            text = text.replace(".", "")  # thousands separator, as in 1.234,5
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return math.nan


def _split(line: str, delimiter: str) -> list[str]:
    return [cell.strip().strip('"').strip() for cell in line.rstrip("\r\n").split(delimiter)]


def detect_delimiter(lines: list[str]) -> str:
    sample = [line for line in lines if line.strip()]
    if not sample:
        return ";"
    for candidate in (";", "\t", ","):
        if sum(1 for line in sample if candidate in line) / len(sample) >= 0.8:
            return candidate
    return max((";", "\t", ","), key=lambda d: sum(line.count(d) for line in sample))


def detect_decimal(rows: list[list[str]], delimiter: str) -> str:
    if delimiter == ",":
        return "."
    comma = dot = 0
    for row in rows[:400]:
        for cell in row:
            if re.fullmatch(r"[-+]?\d+,\d+", cell):
                comma += 1
            elif re.fullmatch(r"[-+]?\d+\.\d+", cell):
                dot += 1
    return "," if comma > dot else "."


def _is_row_of_numbers(row: list[str], decimal: str) -> bool:
    cells = [c for c in row if c != ""]
    if not cells:
        return False
    numbers = sum(1 for c in cells if not math.isnan(parse_number(c, decimal)))
    return numbers >= 1 and numbers >= 0.6 * len(cells)


def _column_values(rows: list[list[str]], index: int, decimal: str) -> np.ndarray:
    return np.array([parse_number(r[index], decimal) if index < len(r) else math.nan for r in rows],
                    dtype=float)


def _is_counter(values: np.ndarray) -> bool:
    """1, 2, 3, ... : a row counter, not a time and not a measurement."""
    finite = values[np.isfinite(values)]
    if finite.size < 10:
        return False
    return bool(np.all(np.diff(finite) == 1.0) and np.all(finite == np.round(finite)))


def _is_time_like(values: np.ndarray) -> bool:
    finite = values[np.isfinite(values)]
    if finite.size < 10:
        return False
    diffs = np.diff(finite)
    if float(np.mean(diffs >= 0)) < 0.999:
        return False
    mean = float(np.mean(diffs))
    if mean <= 0:
        return False
    return float(np.std(diffs)) / mean < 0.5


def _header_name(header: list[str] | None, index: int) -> str:
    return header[index] if header and index < len(header) else ""


def detect_layout(text: str, encoding: str = "utf-8") -> Layout:
    head_lines = list(itertools.islice(io.StringIO(text), 400))
    if not any(line.strip() for line in head_lines):
        raise IngestError("The file is empty.")
    delimiter = detect_delimiter(head_lines)
    head_rows = [_split(line, delimiter) for line in head_lines]
    decimal = detect_decimal(head_rows, delimiter)

    data_line = None
    for i in range(len(head_rows) - MIN_ROWS + 1):
        if all(_is_row_of_numbers(head_rows[k], decimal) for k in range(i, i + MIN_ROWS)):
            data_line = i
            break
    if data_line is None:
        raise IngestError(f"No table of numbers was found. The file needs at least {MIN_ROWS} rows of "
                          "numbers, one row per reading and one column per sensor.")

    rows = [_split(line, delimiter) for line in
            itertools.islice(io.StringIO(text), data_line, data_line + 400) if line.strip()]
    counts = [len(r) for r in rows]
    column_count = max(set(counts), key=lambda n: (counts.count(n), n))
    rows = [r for r in rows if len(r) == column_count] or rows
    share = []
    for j in range(column_count):
        parsed = [parse_number(r[j], decimal) for r in rows if j < len(r)]
        share.append(sum(1 for v in parsed if math.isfinite(v)) / max(len(parsed), 1))

    header = None
    for j in range(data_line - 1, max(-1, data_line - 8), -1):
        cells = _split(head_lines[j], delimiter)
        non_empty = [c for c in cells if c]
        if len(non_empty) >= 2 and sum(1 for c in non_empty
                                       if not math.isnan(parse_number(c, decimal))) < len(non_empty) / 2:
            header = cells
            break

    time_index: int | None = None
    index_columns: list[int] = []
    for j in range(column_count):
        if share[j] < 0.95:
            continue
        values = _column_values(rows, j, decimal)
        name = _header_name(header, j)
        if _is_counter(values) or INDEX_NAME.search(name):
            index_columns.append(j)
            continue
        if (j == 0 or TIME_NAME.search(name)) and _is_time_like(values):
            time_index = j
            break

    warnings: list[str] = []
    time_unit, guessed, rate = "s", True, None
    if time_index is not None:
        name = _header_name(header, time_index)
        match = UNIT_RE.search(name)
        if match and match.group(1).strip().lower() in TIME_UNIT_FACTOR:
            time_unit, guessed = match.group(1).strip().lower(), False
        values = _column_values(rows, time_index, decimal)
        steps = np.diff(values[np.isfinite(values)])
        step = float(np.median(steps)) * TIME_UNIT_FACTOR[time_unit] if steps.size else 0.0
        if step > 0:
            rate = round(1.0 / step, 3)
        if guessed:
            warnings.append("The time column has no unit in its header, so it is read as seconds. "
                            "Change the time unit in the mapping if that is wrong.")
    else:
        warnings.append("No time column was found. Enter the sample rate in Hz so the readings can be placed in time.")
    for j in index_columns:
        warnings.append(f"Column {j + 1} counts the rows instead of measuring, so it is not used as a sensor.")

    total_lines = text.count("\n") + (0 if text.endswith("\n") else 1)
    return Layout(encoding=encoding, delimiter=delimiter, decimal=decimal, data_line=data_line,
                  header=header, column_count=column_count, numeric_share=share,
                  time_index=time_index, time_unit=time_unit, time_unit_guessed=guessed,
                  rate_hz=rate, index_columns=index_columns,
                  data_rows=max(0, total_lines - data_line), warnings=warnings)


def _column_name(header: list[str] | None, index: int, number: int) -> tuple[str, str | None]:
    raw = _header_name(header, index)
    match = UNIT_RE.search(raw)
    unit = normalize_unit(match.group(1)) if match else None
    name = UNIT_RE.sub("", raw).strip(" ;:,") or f"Sensor {number}"
    return name[:40], unit


def suggest_mapping(layout: Layout, default_rpm: float = 0.0) -> Mapping:
    sensors: list[SensorColumn] = []
    warnings = list(layout.warnings)
    for j in range(layout.column_count):
        if j == layout.time_index or j in layout.index_columns or layout.numeric_share[j] < 0.5:
            continue
        name, unit = _column_name(layout.header, j, len(sensors) + 1)
        if unit is None:
            unit = MICRO
            warnings.append(f"{name}: no unit in the header, so the values are read as µm. "
                            "Change the unit if that is wrong.")
        elif unit not in LENGTH_UNITS:
            warnings.append(f"{name}: '{unit}' is not a length unit. Choose a unit or leave the column out "
                            "before analysing.")
        sensors.append(SensorColumn(index=j, name=name, unit=unit,
                                    scale=LENGTH_UNITS.get(unit, 1.0)))
    if not sensors:
        raise IngestError("No numeric sensor column was found.")
    return Mapping(delimiter=layout.delimiter, decimal=layout.decimal, data_line=layout.data_line,
                   time_index=layout.time_index, time_unit=layout.time_unit, rate_hz=None,
                   part_rpm=float(default_rpm), sensors=sensors, warnings=warnings)


def inspect(raw: bytes, default_rpm: float = 0.0) -> dict[str, Any]:
    """Everything the upload dialog needs: the detected layout, a suggested mapping, a preview."""
    if len(raw) > MAX_BYTES:
        raise IngestError("The file is larger than 250 MB. Export a shorter part of the recording.")
    if not raw.strip():
        raise IngestError("The file is empty.")
    text, encoding = decode(raw)
    layout = detect_layout(text, encoding)
    mapping = suggest_mapping(layout, default_rpm)
    preview_lines = list(itertools.islice(io.StringIO(text), max(0, layout.data_line - 2),
                                          layout.data_line + 6))
    preview = [_split(line, layout.delimiter)[:12] for line in preview_lines if line.strip()]
    rate = layout.rate_hz
    duration = layout.data_rows / rate if rate else None
    return {
        "layout": {
            "encoding": layout.encoding, "delimiter": layout.delimiter, "decimal": layout.decimal,
            "data_line": layout.data_line, "header": layout.header, "column_count": layout.column_count,
            "time_column": layout.time_index, "time_unit": layout.time_unit,
            "time_unit_guessed": layout.time_unit_guessed, "rate_hz": rate,
            "counter_columns": layout.index_columns, "data_rows": layout.data_rows,
            "duration_s": None if duration is None else round(duration, 2),
        },
        "mapping": mapping.to_dict(),
        "preview": preview,
        "warnings": mapping.warnings,
    }


# -- loading -------------------------------------------------------------------

def load(text: str, mapping: Mapping, rate_out: int, name: str = "recording.csv",
         progress: Callable[[float, str], None] | None = None) -> Recording:
    """Parse the chosen columns and put them on one grid at ``rate_out`` Hz."""
    included = [s for s in mapping.sensors if s.include]
    needed = [s.index for s in included]
    if mapping.time_index is not None:
        needed = [mapping.time_index, *needed]
    columns: dict[int, list[float]] = {j: [] for j in needed}
    rows = 0
    total = max(len(text), 1)
    consumed = 0
    for line in itertools.islice(io.StringIO(text), mapping.data_line, None):
        consumed += len(line)
        if not line.strip():
            continue
        rows += 1
        if rows > MAX_ROWS:
            raise IngestError("The file has more than 20 million rows. Export a shorter part.")
        cells = line.split(mapping.delimiter)
        for j in needed:
            columns[j].append(parse_number(cells[j], mapping.decimal) if j < len(cells) else math.nan)
        if progress and rows % 200_000 == 0:
            progress(0.05 + 0.25 * consumed / total, "Reading the file")
    if rows == 0:
        raise IngestError("The file has no data rows below the header.")
    arrays = {j: np.array(v, dtype=float) for j, v in columns.items()}
    warnings: list[str] = []

    if mapping.time_index is not None:
        t = arrays[mapping.time_index] * TIME_UNIT_FACTOR[mapping.time_unit]
        keep = np.isfinite(t)
        dropped = int(rows - keep.sum())
        if dropped:
            warnings.append(f"{dropped} rows without a valid time were skipped.")
        t = t[keep]
        arrays = {j: v[keep] for j, v in arrays.items()}
        if t.size < 2:
            raise IngestError("The time column has fewer than two valid values.")
        order = np.argsort(t, kind="stable")
        if not np.array_equal(order, np.arange(order.size)):
            warnings.append("Time values were out of order and have been sorted.")
            t = t[order]
            arrays = {j: v[order] for j, v in arrays.items()}
        steps = np.diff(t)
        positive = steps[steps > 0]
        if positive.size == 0:
            raise IngestError("All time values are the same, so the rate cannot be worked out.")
        source_rate = 1.0 / float(np.median(positive))
        t = t - t[0]
    else:
        if not mapping.rate_hz or mapping.rate_hz <= 0:
            raise IngestError("This file has no time column. Enter its sample rate in Hz.")
        source_rate = float(mapping.rate_hz)
        t = np.arange(rows, dtype=float) / source_rate

    duration = float(t[-1])
    if duration <= 0:
        raise IngestError("The recording is too short to analyse.")
    grid = np.arange(0.0, duration, 1.0 / rate_out)
    nearest = np.clip(np.searchsorted(t, grid, side="left"), 0, t.size - 1)

    sensors_out: list[dict[str, Any]] = []
    values: dict[str, np.ndarray] = {}
    for n, column in enumerate(included, start=1):
        sid = f"s{n}"
        raw_values = arrays[column.index] * column.scale
        finite = np.isfinite(raw_values)
        if not finite.any():
            raise IngestError(f"{column.name} has no numeric values.")
        offset = 0.0
        if column.zero == "start":
            head = finite & (t < 10.0)
            offset = float(np.median(raw_values[head])) if head.any() else float(np.median(raw_values[finite]))
        um = raw_values - offset
        mask = np.isfinite(um)
        resampled = np.interp(grid, t[mask], um[mask]) if mask.sum() >= 2 else np.full(grid.size, np.nan)
        resampled[~mask[nearest]] = np.nan
        values[sid] = resampled
        invalid = float(np.mean(~np.isfinite(resampled))) if resampled.size else 1.0
        if invalid > 0.5:
            warnings.append(f"{column.name}: more than half of the readings are invalid.")
        elif invalid > 0.01:
            warnings.append(f"{column.name}: {invalid * 100:.1f} % of the readings are invalid.")
        sensors_out.append({
            "id": sid, "name": column.name, "file_column": column.index + 1, "unit": MICRO,
            "file_unit": column.unit, "fixture": column.fixture,
            "tolerance": {"low": column.tolerance_low, "high": column.tolerance_high},
            "tolerance_source": column.tolerance_source, "offset_um": round(offset, 4),
            "scale": column.scale, "invalid_share": round(invalid, 4),
        })
        if progress:
            progress(0.3 + 0.1 * n / len(included), "Putting the sensors on one time grid")
    header_lines = [line.strip() for line in itertools.islice(io.StringIO(text), mapping.data_line)]
    metadata = [line for line in header_lines if line][:20]
    return Recording(name=name, rate_hz=int(rate_out), duration_s=round(duration, 3),
                     source_rate_hz=round(source_rate, 3), rows=rows, sensors=sensors_out,
                     values=values, warnings=mapping.warnings + warnings, metadata=metadata)
