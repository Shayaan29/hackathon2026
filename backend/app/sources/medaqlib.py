"""Live data from a real Micro-Epsilon sensor through MEDAQLib (template).

MEDAQLib is Micro-Epsilon's free driver library. It ships as a Windows DLL
with a C interface and examples for several languages, Python included:
https://www.micro-epsilon.com/service/software-sensorintegration/medaqlib/

Why this is a template: the exact function names, the sensor type constant
and the interface parameters depend on the sensor you get at the event. Open
the Python example that comes with MEDAQLib, copy its connection steps into
``_connect`` and its read loop into ``_read_raw``, and you are done. Everything
around it (scaling, invalid readings, resampling to the analysis rate, the
fallback to the simulator) is already handled here.

Typical MEDAQLib flow, as described in its documentation:

1. Load the DLL and create a sensor instance for your sensor type.
2. Set the interface parameters (for Ethernet sensors: the IP address).
3. Open the sensor.
4. In a loop: ask how many values are available, transfer them, and convert
   raw values to scaled values in mm.
5. Close the sensor and release the instance on shutdown.

Configuration in sensors.yaml:

    source:
      type: medaqlib
      dll_path: C:/MEDAQLib/MEDAQLib.dll
      sensor_type: SENSOR_ILD1900     # from the MEDAQLib docs for your sensor
      ip_address: 169.254.168.150    # Ethernet sensors only
      sensor_rate_hz: 2000            # measuring rate set on the sensor
      scale: 1000.0                   # mm -> µm
      offset: 0.0                     # reference subtracted after scaling
"""

from __future__ import annotations

import ctypes
import math
import time
from pathlib import Path
from typing import Any

import numpy as np

from ..config import SensorConfig
from .base import SensorSource, SourceError


class MedaqlibSource(SensorSource):
    kind = "medaqlib"

    def __init__(self, cfg: SensorConfig, rate_hz: int) -> None:
        super().__init__(cfg, rate_hz)
        opts = cfg.source
        self.dll_path = Path(str(opts.get("dll_path", "MEDAQLib.dll")))
        self.sensor_type = str(opts.get("sensor_type", ""))
        self.ip_address = str(opts.get("ip_address", ""))
        self.sensor_rate_hz = float(opts.get("sensor_rate_hz", rate_hz))
        self.scale = float(opts.get("scale", 1000.0))
        self.offset = float(opts.get("offset", 0.0))
        self._lib: Any = None
        self._handle: Any = None
        self._carry = np.empty(0)
        self._t_last: float | None = None

    # -- the two methods to fill in at the event ---------------------------

    def _connect(self) -> None:
        """Create the sensor instance, set the interface and open the sensor.

        Replace the body with the connection steps from MEDAQLib's Python
        example. Store whatever handle you need in ``self._handle``.
        """
        raise SourceError(
            "MEDAQLib source is a template: fill in _connect() and _read_raw() "
            "in backend/app/sources/medaqlib.py (see the docstring)."
        )

    def _read_raw(self) -> np.ndarray:
        """Return all values that arrived since the last call, in mm.

        Use NaN for readings the sensor marks as invalid. Return an empty
        array when nothing is new. Replace the body with the read loop from
        MEDAQLib's Python example.
        """
        raise SourceError("MEDAQLib _read_raw() not implemented yet")

    def _disconnect(self) -> None:
        """Close the sensor and release the instance."""

    # -- everything below works as is --------------------------------------

    def start(self) -> None:
        if not self.dll_path.exists():
            raise SourceError(f"MEDAQLib DLL not found at {self.dll_path}")
        try:
            self._lib = ctypes.WinDLL(str(self.dll_path))  # type: ignore[attr-defined]
        except (AttributeError, OSError) as exc:
            raise SourceError(f"could not load MEDAQLib ({exc}); it needs Windows") from exc
        self._connect()
        self._t_last = time.time()

    def stop(self) -> None:
        try:
            self._disconnect()
        finally:
            self._handle = None
            self._lib = None

    def read(self, now: float) -> tuple[np.ndarray, float]:
        raw_mm = np.asarray(self._read_raw(), dtype=float)
        if raw_mm.size == 0:
            return np.empty(0), self._t_last or now
        values = raw_mm * self.scale - self.offset
        values = self._resample(values)
        self._t_last = now
        return values, now

    def _resample(self, values: np.ndarray) -> np.ndarray:
        """Convert from the sensor's rate to the analysis rate."""
        if math.isclose(self.sensor_rate_hz, self.rate_hz):
            return values
        data = np.concatenate((self._carry, values))
        ratio = self.sensor_rate_hz / self.rate_hz
        n_out = int(data.size / ratio)
        if n_out == 0:
            self._carry = data
            return np.empty(0)
        if ratio > 1:
            # block average keeps noise realistic and removes aliasing
            step = int(round(ratio))
            usable = n_out * step
            block = data[:usable].reshape(n_out, step)
            with np.errstate(all="ignore"):
                out = np.nanmean(block, axis=1)
            self._carry = data[usable:]
            return out
        idx = np.arange(n_out) * ratio
        out = np.interp(idx, np.arange(data.size), data)
        self._carry = np.empty(0)
        return out
