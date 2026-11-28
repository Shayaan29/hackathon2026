"""Any Micro-Epsilon sensor through MEDAQLib: plug it in over Ethernet or USB, and the cockpit finds it.

MEDAQLib is Micro-Epsilon's free driver library (a Windows DLL). This source loads it, lets its SensorFinder look
for a sensor on Ethernet, USB and serial ports, opens the first one it finds and streams its distance readings.
The cockpit then shows which sensor it found: model, interface, address and serial number.

    source:
      type: medaqlib
      dll_path: C:/MEDAQLib/Release-x64/MEDAQLib.dll   # the 64-bit DLL for 64-bit Python; leave out to look
                                                        # in the usual places and in MEDAQLIB_DLL
      sensor: auto              # auto: search for the models in SEARCH_ORDER; or one model, such as ILD5500
      address: ""               # optional: no search, open this IP address (Ethernet) or COM port (USB/serial)

The steps follow the MEDAQLib manual (chapter 8.3, SensorFinder commands) and its SensorFinder sample:
Start_FindSensor, Get_FindSensorProgress until 100 %, Get_FoundSensor per index, then OpenSensor with the found
interface and address, DataAvail and TransferData in a loop. Readings MEDAQLib cannot scale (no target, out of
range) arrive as a large negative number and are shown as invalid.

This was written from the manual without the hardware at hand. For the optoNCDT 5500 the direct Ethernet
connection (type: ild5500) is the tested one; this source is for plugging in other Micro-Epsilon sensors.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

import numpy as np

from ..config import SensorConfig
from .base import SensorSource, SourceError
from .resample import Resampler

log = logging.getLogger("cockpit.medaqlib")

# MEDAQLib.h, enum ME_SENSOR: the models the SensorFinder can look for (manual chapter 8.3.1)
SENSOR_TYPES = {
    "ILD5500": 71, "ILD1900": 58, "ILD2300": 29, "ILD1420": 42, "ILD1320": 41, "ILD1220": 56, "ILD1750": 51,
    "IFD2410": 64, "IFD2411": 65, "IFD2412": 76, "IFD2415": 66, "IFD2416": 67, "IFD2417": 77, "IFD2421": 46,
    "IFD2422": 47, "IFD2445": 39, "IFD2451": 30, "IFD2461": 44, "IFD2465": 62, "IFD2466": 63, "IFD2471": 26,
    "ODC2520": 37, "ODC2700": 70, "DT3060": 50, "DT3100": 28, "IMC5400": 55, "IMC5600": 59, "ACS7000": 35,
    "ILR2250": 60, "ILR3800": 72,
}
# the search order for "sensor: auto": the hackathon's sensor first, then the other distance sensors
SEARCH_ORDER = ("ILD5500", "ILD1900", "ILD2300", "ILD1420", "ILD1320", "ILD1220", "ILD1750", "IFD2421",
                "IFD2422", "IFD2451", "IFD2461", "IFD2471", "IFD2410", "IFD2415", "ODC2520", "ODC2700", "DT3060",
                "DT3100")
DLL_PLACES = (
    "C:/Program Files/MEDAQLib/Release-x64/MEDAQLib.dll",
    "C:/Program Files (x86)/MEDAQLib/Release-x64/MEDAQLib.dll",
    "C:/Program Files/Micro-Epsilon/MEDAQLib/Release-x64/MEDAQLib.dll",
    "C:/Program Files (x86)/Micro-Epsilon/MEDAQLib/Release-x64/MEDAQLib.dll",
    "C:/MEDAQLib/Release-x64/MEDAQLib.dll",
    "C:/MEDAQLib/MEDAQLib.dll",
)
ERR_NOERROR = 0
ERR_WARNING = -26
INVALID_BELOW = -1e9          # MEDAQLib puts invalid readings at -DBL_MAX or at -1e10 minus an error number
SEARCH_TIMEOUT_S = 25.0       # per model
BLOCK = 20000                 # values per TransferData call
RATE_WINDOW_S = 3.0
SILENCE_S = 2.0


class MedaqlibError(SourceError):
    pass


class Library(Protocol):
    """The MEDAQLib calls this source uses (the DLL through ctypes, or a stand-in in the tests)."""

    def create(self, sensor_type: int) -> int: ...
    def release(self, handle: int) -> int: ...
    def set_int(self, handle: int, name: str, value: int) -> int: ...
    def set_str(self, handle: int, name: str, value: str) -> int: ...
    def get_int(self, handle: int, name: str) -> tuple[int, int]: ...
    def get_str(self, handle: int, name: str) -> tuple[int, str]: ...
    def exec_cmd(self, handle: int, command: str) -> int: ...
    def set_int_exec(self, handle: int, command: str, name: str, value: int) -> int: ...
    def exec_get_double(self, handle: int, command: str, name: str) -> tuple[int, float]: ...
    def open(self, handle: int) -> int: ...
    def close(self, handle: int) -> int: ...
    def data_avail(self, handle: int) -> tuple[int, int]: ...
    def transfer(self, handle: int, max_values: int) -> tuple[int, np.ndarray]: ...
    def error_text(self, handle: int) -> str: ...


class DllLibrary:
    """MEDAQLib.dll through ctypes, with the declarations of MEDAQLib.h (all functions are WINAPI)."""

    def __init__(self, path: Path) -> None:
        loader = getattr(ctypes, "WinDLL", None)
        if loader is None:
            raise MedaqlibError("MEDAQLib is a Windows library: run the cockpit on Windows to use it.")
        try:
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(path.parent))        # MEDAQLib loads helpers from its own folder
            self.dll = loader(str(path))
        except OSError as exc:
            hint = (" The DLL must match Python: 64-bit Python needs Release-x64/MEDAQLib.dll."
                    if sys.maxsize > 2 ** 32 else "")
            raise MedaqlibError(f"Could not load MEDAQLib from {path} ({exc}).{hint}") from exc
        u32, i32, dbl, s = ctypes.c_uint32, ctypes.c_int32, ctypes.c_double, ctypes.c_char_p
        p_i32, p_u32, p_dbl = ctypes.POINTER(i32), ctypes.POINTER(u32), ctypes.POINTER(dbl)
        self._sig("CreateSensorInstance", [i32], u32)
        self._sig("ReleaseSensorInstance", [u32], i32)
        self._sig("SetParameterInt", [u32, s, i32], i32)
        self._sig("SetParameterString", [u32, s, s], i32)
        self._sig("GetParameterInt", [u32, s, p_i32], i32)
        self._sig("GetParameterString", [u32, s, s, p_u32], i32)
        self._sig("ExecSCmd", [u32, s], i32)
        self._sig("SetIntExecSCmd", [u32, s, s, i32], i32)
        self._sig("ExecSCmdGetDouble", [u32, s, s, p_dbl], i32)
        self._sig("OpenSensor", [u32], i32)
        self._sig("CloseSensor", [u32], i32)
        self._sig("DataAvail", [u32, p_i32], i32)
        self._sig("TransferData", [u32, p_i32, p_dbl, i32, p_i32], i32)
        self._sig("GetError", [u32, s, u32], i32)

    def _sig(self, name: str, args: list[Any], result: Any) -> None:
        fn = getattr(self.dll, name)
        fn.argtypes, fn.restype = args, result

    @staticmethod
    def _b(text: str) -> bytes:
        return text.encode("ascii")

    def create(self, sensor_type: int) -> int:
        return int(self.dll.CreateSensorInstance(sensor_type))

    def release(self, handle: int) -> int:
        return int(self.dll.ReleaseSensorInstance(handle))

    def set_int(self, handle: int, name: str, value: int) -> int:
        return int(self.dll.SetParameterInt(handle, self._b(name), int(value)))

    def set_str(self, handle: int, name: str, value: str) -> int:
        return int(self.dll.SetParameterString(handle, self._b(name), value.encode("cp1252", "replace")))

    def get_int(self, handle: int, name: str) -> tuple[int, int]:
        value = ctypes.c_int32(0)
        err = self.dll.GetParameterInt(handle, self._b(name), ctypes.byref(value))
        return int(err), int(value.value)

    def get_str(self, handle: int, name: str) -> tuple[int, str]:
        size = ctypes.c_uint32(1024)
        buf = ctypes.create_string_buffer(size.value)
        err = self.dll.GetParameterString(handle, self._b(name), buf, ctypes.byref(size))
        return int(err), buf.value.decode("cp1252", "replace")

    def exec_cmd(self, handle: int, command: str) -> int:
        return int(self.dll.ExecSCmd(handle, self._b(command)))

    def set_int_exec(self, handle: int, command: str, name: str, value: int) -> int:
        return int(self.dll.SetIntExecSCmd(handle, self._b(command), self._b(name), int(value)))

    def exec_get_double(self, handle: int, command: str, name: str) -> tuple[int, float]:
        value = ctypes.c_double(0.0)
        err = self.dll.ExecSCmdGetDouble(handle, self._b(command), self._b(name), ctypes.byref(value))
        return int(err), float(value.value)

    def open(self, handle: int) -> int:
        return int(self.dll.OpenSensor(handle))

    def close(self, handle: int) -> int:
        return int(self.dll.CloseSensor(handle))

    def data_avail(self, handle: int) -> tuple[int, int]:
        value = ctypes.c_int32(0)
        err = self.dll.DataAvail(handle, ctypes.byref(value))
        return int(err), int(value.value)

    def transfer(self, handle: int, max_values: int) -> tuple[int, np.ndarray]:
        raw = (ctypes.c_int32 * max_values)()
        scaled = (ctypes.c_double * max_values)()
        got = ctypes.c_int32(0)
        err = self.dll.TransferData(handle, raw, scaled, max_values, ctypes.byref(got))
        n = max(0, min(int(got.value), max_values))
        return int(err), np.ctypeslib.as_array(scaled)[:n].astype(float)

    def error_text(self, handle: int) -> str:
        buf = ctypes.create_string_buffer(1024)
        self.dll.GetError(handle, buf, 1024)
        return buf.value.decode("cp1252", "replace").strip()


def _in_folder(folder: Path) -> list[Path]:
    """Where MEDAQLib.dll sits inside an unpacked MEDAQLib folder (64-bit first)."""
    return [folder / "Release-x64" / "MEDAQLib.dll", folder / "MEDAQLib.dll",
            folder / "Snippets" / "Python" / "MEDAQLib.dll"]


def find_dll(configured: str | None) -> Path:
    """The DLL to load: the configured file or folder, MEDAQLIB_DLL, the usual install places, or a
    MEDAQLib-* folder in Documents (also in OneDrive)."""
    candidates: list[Path] = []
    for item in (configured, os.environ.get("MEDAQLIB_DLL", "")):
        if item:
            path = Path(item.strip().strip('"'))
            candidates += _in_folder(path) if path.is_dir() else [path]
    candidates += [Path(x) for x in DLL_PLACES]
    home = Path.home()
    for docs in (home / "Documents", home / "OneDrive" / "Documents", home / "OneDrive" / "Dokumente",
                 home / "Dokumente", home / "Downloads"):
        if docs.is_dir():
            for folder in sorted(docs.glob("MEDAQLib*"), reverse=True):
                if folder.is_dir():
                    candidates += _in_folder(folder)
    for path in candidates:
        if path.is_file():
            return path
    if configured:
        raise MedaqlibError(f"MEDAQLib.dll was not found at {configured}. Install MEDAQLib from the Micro-Epsilon "
                            "website and set source.dll_path to Release-x64/MEDAQLib.dll.")
    raise MedaqlibError("MEDAQLib.dll was not found. Install MEDAQLib from the Micro-Epsilon website and set "
                        "source.dll_path (or MEDAQLIB_DLL) to its Release-x64/MEDAQLib.dll.")


@dataclass
class Found:
    """A sensor the SensorFinder reported."""

    type_id: int
    model: str
    interface: str
    address: str              # IP address or COM port
    serial: str
    baudrate: int | None = None

    def describe(self) -> str:
        where = f"{self.interface} {self.address}".strip()
        serial = f", serial {self.serial}" if self.serial else ""
        return f"{self.model} on {where}{serial}"


class MedaqlibSource(SensorSource):
    kind = "medaqlib"

    def __init__(self, cfg: SensorConfig, rate_hz: int,
                 library: Callable[[Path], Library] | None = None) -> None:
        super().__init__(cfg, rate_hz)
        opts = cfg.source
        self.dll_setting = str(opts.get("dll_path") or "").strip() or None
        wanted = str(opts.get("sensor", "auto")).strip().upper().removeprefix("SENSOR_")
        if wanted != "AUTO" and wanted not in SENSOR_TYPES:
            raise SourceError(f"{cfg.id}: source.sensor must be auto or one of {', '.join(sorted(SENSOR_TYPES))}")
        self.models = SEARCH_ORDER if wanted == "AUTO" else (wanted,)
        self.address = str(opts.get("address") or "").strip()
        self._library_factory = library or DllLibrary
        self._lib: Library | None = None
        self._handle = 0
        self._lock = threading.Lock()
        self._queue: deque[np.ndarray] = deque()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._resampler = Resampler(rate_hz)
        self._arrivals: deque[tuple[float, int]] = deque()
        self.found: Found | None = None
        self.state = "offline"
        self.message = "Starting MEDAQLib"
        self.samples = 0
        self.invalid = 0
        self.measured_rate: float | None = None
        self._t_last: float | None = None
        self.channel = 1
        self.per_frame = 1
        self.to_um = 1000.0

    # -- lifecycle -------------------------------------------------------------------

    def start(self) -> None:
        self.stop()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"medaqlib-{self.cfg.id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._close()

    def _set(self, state: str, message: str) -> None:
        if (state, message) != (self.state, self.message):
            log.info("%s: %s", self.cfg.id, message)
        self.state, self.message = state, message

    def _error(self, what: str, err: int) -> str:
        text = self._lib.error_text(self._handle) if self._lib is not None and self._handle else ""
        return f"{what} failed (MEDAQLib error {err}{': ' + text if text else ''})"

    def _close(self) -> None:
        lib, handle = self._lib, self._handle
        self._handle = 0
        if lib is not None and handle:
            try:
                lib.close(handle)
                lib.release(handle)
            except Exception:  # noqa: BLE001 - closing must never raise
                log.exception("%s: closing MEDAQLib failed", self.cfg.id)

    # -- the worker thread ---------------------------------------------------------------

    def _run(self) -> None:
        try:
            self._lib = self._library_factory(find_dll(self.dll_setting))
        except SourceError as exc:
            self._set("offline", str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - shown on the screen instead of a crash
            self._set("offline", f"Could not load MEDAQLib: {exc}")
            return
        delay = 1.0
        while not self._stop.is_set():
            try:
                found = self._find()
                if found is None:
                    self._set("offline", "No Micro-Epsilon sensor found on Ethernet, USB or the serial ports. "
                                         "Check the cable and the power, then it searches again.")
                else:
                    self._open(found)
                    delay = 1.0
                    self._stream()
            except MedaqlibError as exc:
                self._set("offline", str(exc))
            except Exception as exc:  # noqa: BLE001
                log.exception("%s: MEDAQLib", self.cfg.id)
                self._set("offline", f"MEDAQLib stopped: {exc}")
            finally:
                self._close()
            if self._stop.wait(delay):
                break
            delay = min(delay * 2, 8.0)

    def _find(self) -> Found | None:
        """The configured address, or the first sensor the SensorFinder reports."""
        assert self._lib is not None
        lib = self._lib
        if self.address:
            model = self.models[0]
            interface = "TCP/IP" if self.address.count(".") == 3 else "RS232"
            return Found(SENSOR_TYPES[model], model, interface, self.address, "")
        for model in self.models:
            if self._stop.is_set():
                return None
            self._set("offline", f"Looking for an {model} on Ethernet, USB and the serial ports")
            handle = lib.create(SENSOR_TYPES[model])
            if not handle:
                raise MedaqlibError(f"MEDAQLib could not create an instance for {model}")
            try:
                err = lib.set_int_exec(handle, "Start_FindSensor", "IP_SensorType", SENSOR_TYPES[model])
                if err not in (ERR_NOERROR, ERR_WARNING):
                    log.info("%s: search for %s not possible (error %d)", self.cfg.id, model, err)
                    continue
                started = time.monotonic()
                while not self._stop.is_set():
                    err, progress = lib.exec_get_double(handle, "Get_FindSensorProgress", "IA_Progress")
                    if err not in (ERR_NOERROR, ERR_WARNING) or progress >= 100.0:
                        break
                    if time.monotonic() - started > SEARCH_TIMEOUT_S:
                        log.info("%s: search for %s timed out at %.0f %%", self.cfg.id, model, progress)
                        break
                    self._set("offline", f"Looking for an {model}: {progress:.0f} %")
                    time.sleep(0.1)
                err, count = lib.get_int(handle, "IA_Found")
                if err != ERR_NOERROR or count <= 0:
                    continue
                for index in range(count):
                    if lib.set_int_exec(handle, "Get_FoundSensor", "IP_Index", index) != ERR_NOERROR:
                        continue
                    _, type_id = lib.get_int(handle, "IP_SensorType")
                    _, name = lib.get_str(handle, "IP_SensorTypeName")
                    _, interface = lib.get_str(handle, "IP_Interface")
                    address = ""
                    if interface in ("TCP/IP", "IF2008_ETH"):
                        _, address = lib.get_str(handle, "IP_RemoteAddr")
                    elif interface in ("RS232", "IF2004_USB"):
                        _, address = lib.get_str(handle, "IP_Port")
                    err, baud = lib.get_int(handle, "IP_Baudrate")
                    _, serial = lib.get_str(handle, "SA_SerialNumber")
                    return Found(type_id or SENSOR_TYPES[model], name or model, interface, address, serial.strip(),
                                 baud if err == ERR_NOERROR and baud > 0 else None)
            finally:
                lib.release(handle)
        return None

    def _open(self, found: Found) -> None:
        assert self._lib is not None
        lib = self._lib
        self._set("offline", f"Opening {found.describe()}")
        handle = lib.create(found.type_id)
        if not handle:
            raise MedaqlibError(f"MEDAQLib could not create an instance for {found.model}")
        self._handle = handle
        lib.set_str(handle, "IP_Interface", found.interface)
        if found.interface in ("TCP/IP", "IF2008_ETH"):
            lib.set_str(handle, "IP_RemoteAddr", found.address)
        elif found.address:
            lib.set_str(handle, "IP_Port", found.address)
        if found.baudrate:
            lib.set_int(handle, "IP_Baudrate", found.baudrate)
        lib.set_int(handle, "IP_AutomaticMode", 3)         # MEDAQLib reads the sensor setup and starts the output
        err = lib.open(handle)
        if err not in (ERR_NOERROR, ERR_WARNING):
            raise MedaqlibError(self._error(f"Opening {found.describe()}", err))
        self.found = found
        self._layout()

    def _layout(self) -> None:
        """Which value of a frame is the distance, and its unit (Get_TransmittedDataInfo)."""
        assert self._lib is not None
        lib, handle = self._lib, self._handle
        self.channel, self.per_frame, self.to_um = 1, 1, 1000.0
        if lib.exec_cmd(handle, "Get_TransmittedDataInfo") != ERR_NOERROR:
            return
        err, per_frame = lib.get_int(handle, "IA_ValuesPerFrame")
        if err != ERR_NOERROR or per_frame <= 0:
            return
        self.per_frame = per_frame
        names = [lib.get_str(handle, f"IA_Scaled_Name{i}")[1].lower() for i in range(1, per_frame + 1)]
        units = [lib.get_str(handle, f"IA_Scaled_Unit{i}")[1].strip().lower() for i in range(1, per_frame + 1)]
        pick = next((i for i, n in enumerate(names) if "dist" in n), None)
        if pick is None:
            pick = next((i for i, u in enumerate(units) if u in ("mm", "µm", "um")), 0)
        self.channel = pick + 1
        unit = units[pick] if pick < len(units) else "mm"
        self.to_um = 1.0 if unit in ("µm", "um") else 1000.0
        log.info("%s: %d values per frame %s, distance is value %d (%s)", self.cfg.id, per_frame, names,
                 self.channel, unit or "mm")

    def _stream(self) -> None:
        assert self._lib is not None and self.found is not None
        lib, handle = self._lib, self._handle
        self._set("no_data", f"Connected to {self.found.describe()}, waiting for the first readings")
        last_data = time.monotonic()
        while not self._stop.is_set():
            err, avail = lib.data_avail(handle)
            if err not in (ERR_NOERROR, ERR_WARNING):
                raise MedaqlibError(self._error("Reading from the sensor", err))
            if avail < self.per_frame:
                if time.monotonic() - last_data > SILENCE_S:
                    self._set("no_data", f"Connected to {self.found.describe()}, but no readings arrive.")
                time.sleep(0.01)
                continue
            take = min(BLOCK - BLOCK % self.per_frame, avail - avail % self.per_frame)
            err, scaled = lib.transfer(handle, take)
            if err not in (ERR_NOERROR, ERR_WARNING):
                raise MedaqlibError(self._error("Reading from the sensor", err))
            frames = scaled[: scaled.size - scaled.size % self.per_frame].reshape(-1, self.per_frame)
            if frames.size == 0:
                continue
            last_data = time.monotonic()
            mm = frames[:, self.channel - 1]
            bad = ~np.isfinite(mm) | (mm <= INVALID_BELOW)
            um = np.where(bad, np.nan, mm) * self.to_um
            self.samples += int(um.size)
            self.invalid += int(bad.sum())
            self._note_arrival(time.monotonic(), int(um.size))
            with self._lock:
                self._queue.append(um)
            share = float(bad.mean())
            if share > 0.5:
                self._set("invalid", "Readings arrive, but most are invalid: no target, or it is out of range.")
            else:
                self._set("streaming", f"Live from {self.found.describe()} through MEDAQLib")

    def _note_arrival(self, now: float, n: int) -> None:
        self._arrivals.append((now, n))
        while len(self._arrivals) > 1 and now - self._arrivals[0][0] > RATE_WINDOW_S:
            self._arrivals.popleft()
        first = self._arrivals[0]
        span = now - first[0]
        if len(self._arrivals) > 1 and span >= 0.5:
            self.measured_rate = sum(c for _, c in list(self._arrivals)[1:]) / span

    # -- the cockpit's side ----------------------------------------------------------------------

    def read(self, now: float) -> tuple[np.ndarray, float]:
        with self._lock:
            chunks = list(self._queue)
            self._queue.clear()
        if not chunks or not self.measured_rate:
            if chunks:                                    # keep them until the rate is known
                with self._lock:
                    self._queue.extendleft(reversed(chunks))
            return np.empty(0), self._t_last or now
        out = self._resampler.push(np.concatenate(chunks), self.measured_rate)
        if out.size == 0:
            return np.empty(0), self._t_last or now
        self._t_last = now
        return out, now

    def status(self) -> dict[str, Any]:
        found = self.found
        serial = int(found.serial) if found and found.serial.isdigit() else None
        share = self.invalid / self.samples if self.samples else None
        return {
            "kind": self.kind, "state": self.state, "message": self.message,
            "host": f"{found.model} via {found.interface} {found.address}".strip() if found else "MEDAQLib",
            "port": 0, "rate_hz": round(self.measured_rate) if self.measured_rate else None,
            "article": None, "serial": serial, "samples": self.samples,
            "invalid_percent": round(share * 100, 1) if share is not None else None,
            "last_status": None, "reference": "range_start", "reference_mm": 0.0,
            "intensity_percent": None, "temperature_c": None,
            "model": found.model if found else None, "interface": found.interface if found else None,
        }
