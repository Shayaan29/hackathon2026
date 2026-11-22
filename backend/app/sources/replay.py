"""Replay a CSV recording, for example one exported from Micro-Epsilon sensorTOOL.

This is the no-code way to show real data: record a few minutes with
sensorTOOL, save as CSV, and point the sensor's ``source`` at the file:

    source:
      type: replay
      path: data/sensor1_recording.csv
      column: 1            # column index (0 = first) or the header name
      file_rate_hz: 1000   # sample rate of the recording
      scale: 1000.0        # optional: mm -> µm
      offset: 0.0          # optional: subtracted after scaling
      delimiter: ";"       # optional: detected automatically
      decimal: ","         # optional: "," for German number format
      skip_rows: 0         # optional: lines to skip before the header/data
      loop: true           # start again at the end of the file

Empty cells and text such as "invalid" become invalid readings (NaN).
"""

from __future__ import annotations

import csv
import io
import math
from pathlib import Path
from typing import Any

import numpy as np

from ..config import BACKEND_DIR, SensorConfig
from .base import SensorSource, SourceError


def _parse_number(cell: str, decimal: str) -> float:
    text = cell.strip()
    if not text:
        return math.nan
    if decimal == ",":
        text = text.replace(".", "").replace(",", ".") if text.count(",") == 1 else text
    try:
        return float(text)
    except ValueError:
        return math.nan


def load_csv_column(path: Path, column: int | str, delimiter: str | None = None,
                    decimal: str = ".", skip_rows: int = 0) -> np.ndarray:
    """Read one numeric column of a CSV file into a float array."""
    if not path.exists():
        raise SourceError(f"replay file not found: {path}")
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    lines = raw.splitlines()[skip_rows:]
    if not lines:
        raise SourceError(f"replay file is empty: {path}")
    if delimiter is None:
        sample = "\n".join(lines[:20])
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=";,\t").delimiter
        except csv.Error:
            delimiter = ";" if ";" in lines[0] else ","
    rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=delimiter))

    if isinstance(column, str) and not column.strip().lstrip("-").isdigit():
        header = [h.strip() for h in rows[0]]
        if column not in header:
            raise SourceError(f"column {column!r} not in header {header}")
        index = header.index(column)
        rows = rows[1:]
    else:
        index = int(column)
        first = rows[0][index] if index < len(rows[0]) else ""
        if math.isnan(_parse_number(first, decimal)):
            rows = rows[1:]  # skip a text header

    values = [_parse_number(r[index], decimal) if index < len(r) else math.nan for r in rows]
    data = np.asarray(values, dtype=float)
    if data.size == 0 or not np.isfinite(data).any():
        raise SourceError(f"no numbers found in column {column!r} of {path.name}")
    return data


class CsvReplaySource(SensorSource):
    kind = "replay"

    def __init__(self, cfg: SensorConfig, rate_hz: int) -> None:
        super().__init__(cfg, rate_hz)
        opts = cfg.source
        path = Path(str(opts.get("path", "")))
        self.path = path if path.is_absolute() else BACKEND_DIR / path
        self.column = opts.get("column", 1)
        self.file_rate_hz = float(opts.get("file_rate_hz", rate_hz))
        self.scale = float(opts.get("scale", 1.0))
        self.offset = float(opts.get("offset", 0.0))
        self.delimiter = opts.get("delimiter")
        self.decimal = str(opts.get("decimal", "."))
        self.skip_rows = int(opts.get("skip_rows", 0))
        self.loop = bool(opts.get("loop", True))
        self._data = np.empty(0)
        self._pos = 0
        self._t_last: float | None = None

    def start(self) -> None:
        raw = load_csv_column(self.path, self.column, self.delimiter, self.decimal, self.skip_rows)
        raw = raw * self.scale - self.offset
        if not math.isclose(self.file_rate_hz, self.rate_hz):
            duration = raw.size / self.file_rate_hz
            t_file = np.arange(raw.size) / self.file_rate_hz
            t_new = np.arange(int(duration * self.rate_hz)) / self.rate_hz
            mask = np.isfinite(raw)
            resampled = np.interp(t_new, t_file[mask], raw[mask])
            # keep invalid stretches invalid
            nearest = np.clip(np.round(t_new * self.file_rate_hz).astype(int), 0, raw.size - 1)
            resampled[~mask[nearest]] = np.nan
            raw = resampled
        self._data = raw
        self._pos = 0

    def _take(self, n: int) -> np.ndarray:
        out = np.empty(n)
        filled = 0
        while filled < n:
            if self._pos >= self._data.size:
                if not self.loop:
                    out[filled:] = np.nan
                    break
                self._pos = 0
            chunk = min(n - filled, self._data.size - self._pos)
            out[filled: filled + chunk] = self._data[self._pos: self._pos + chunk]
            self._pos += chunk
            filled += chunk
        return out

    def read(self, now: float) -> tuple[np.ndarray, float]:
        if self._data.size == 0:
            raise SourceError("replay source was not started")
        if self._t_last is None:
            self._t_last = now
            return np.empty(0), now
        n = int((now - self._t_last) * self.rate_hz)
        if n <= 0:
            return np.empty(0), self._t_last
        self._t_last += n / self.rate_hz
        return self._take(n), self._t_last

    def backfill(self, seconds: float, now: float) -> tuple[np.ndarray, float] | None:
        """Show the start of the recording as history so charts are not empty."""
        n = min(int(seconds * self.rate_hz), self._data.size)
        if n <= 0:
            return None
        self._t_last = now
        return self._take(n), now

    def inject(self, kind: str, now: float, **params: Any) -> dict[str, Any]:
        raise SourceError("demo events only work with the simulator; this sensor replays a recording")
