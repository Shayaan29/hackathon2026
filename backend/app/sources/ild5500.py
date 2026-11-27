"""Live data from a Micro-Epsilon optoNCDT 5500 (ILD5500) over Ethernet.

The sensor can offer its readings as a plain TCP stream: in its web page switch the output to Ethernet and set
the measured value transmission to "Server TCP/IP" with a port. This module connects to that port and reads the
stream, no vendor driver needed. The format is the one of the operating instructions, chapter 9 (little endian):

    header, 7 x uint32:  "DATA" (0x41544144), article number, serial number, length of the video data in bytes,
                         length of the measurement data in bytes, number of frames, counter
    then the video data (skipped here) and the frames: one int32 per selected signal, always in the fixed order
    of table 9.1 (SHUTTER, MEASRATE, TIMESTAMP, COUNTER, STATE, UNLIN, INTENSITY, DIST1, ..., TEMPERATURE).

With only the distance (DIST1) selected, a frame is one int32: millimetres = value / 1 000 000. Values above
0x7FFFFEFF are status codes (no target, outside the measuring range, laser off, ...) and become invalid readings.

DIST1 is counted from the start of the measuring range (SMR): 0 mm at SMR, the measuring range in mm at its end
(EMR), negative or larger inside the extended measuring range. That is how the manual defines the distance for
the RS422 output (table 8.1, page 62), and the Ethernet range allows negative values (table 9.1, page 66).

Configuration in a sensors file (see config/sensors.live.yaml):

    source:
      type: ild5500
      host: ${LIVE_SENSOR_HOST:-169.254.168.150}   # factory IP address of the sensor
      port: ${LIVE_SENSOR_PORT:-1024}               # the port set under "Server TCP/IP"
      reference_mm: range_start  # what is zero. range_start = the start of the measuring range: the cockpit then
                              # shows the distance exactly as the sensor's web page does (0 to 10 mm for a -10).
                              # auto = the first second of readings is zero (shows only changes);
                              # mid = the middle of the measuring range; or a distance in mm that is zero
      range_mm: 10            # model ILD5500-10 (10, 25, 50, 100 or 200), used by reference_mm: mid; the model
                              # in the sensor file sets it automatically
      sensor_rate_hz: auto    # measuring rate of the sensor; auto = measured from the stream
      signals: [DIST1]        # the signals ticked in the sensor's Ethernet data output
      invert: false           # true turns the sign (a target moving away becomes negative)
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..config import SensorConfig
from .base import SensorSource, SourceError
from .resample import Resampler

log = logging.getLogger("cockpit.ild5500")

PREAMBLE = 0x41544144                       # "DATA" read as a little endian uint32
PREAMBLE_BYTES = struct.pack("<I", PREAMBLE)
HEADER = struct.Struct("<7I")
HEADER_SIZE = HEADER.size
MAX_VIDEO_BYTES = 1 << 20
MAX_DATA_BYTES = 1 << 22
MAX_FRAMES = 100_000
LAST_VALID = 0x7FFFFEFF                     # larger values are status codes
COUNTS_PER_MM = 1_000_000.0
SILENCE_S = 3.0                             # a connected sensor that sends nothing this long is reported
RATE_WINDOW_S = 3.0
REFERENCE_S = 1.0                           # auto reference: median of this much valid data

# Order of the values in a frame (operating instructions, table 9.1). The sensor always sends them in this order,
# whatever order the boxes were ticked in. VIDEO is not part of a frame, it comes before the frames.
SIGNAL_ORDER = ("ENCODER1", "SHUTTER", "MEASRATE", "TIMESTAMP", "COUNTER", "STATE", "UNLIN", "INTENSITY",
                "DIST1", "TRIGGEREVENTCOUNTER", "TRIGGERVALUECOUNTER", "TEMPERATURE")
SIGNAL_ALIASES = {"MEASTATE": "MEASRATE"}   # the web page of some firmware versions calls it MEASTATE

STATUS_TEXT = {
    0x7FFFFF04: "no peak: no target or too little light",
    0x7FFFFF05: "target closer than the measuring range",
    0x7FFFFF06: "target beyond the measuring range",
    0x7FFFFF08: "reading cannot be evaluated",
    0x7FFFFF09: "peak too wide: surface or light problem",
    0x7FFFFF0A: "laser is off",
}
# which status code means what, in the words the invalid alert uses (table 9.1, page 66)
STATUS_REASON = {
    0x7FFFFF04: "no_peak", 0x7FFFFF05: "before_range", 0x7FFFFF06: "after_range",
    0x7FFFFF08: "cannot_evaluate", 0x7FFFFF09: "peak_too_wide", 0x7FFFFF0A: "laser_off",
}
REASON_WINDOW_S = 2.0                       # the invalid readings of this long are summed up by reason
MODEL_RANGES_MM = (10, 25, 50, 100, 200)    # measuring ranges of the family (operating instructions, chapter 3.4)


def normalise_signals(names: Any) -> list[str]:
    """The ticked signals in the order the sensor sends them. VIDEO is dropped, unknown names raise."""
    if isinstance(names, str):
        names = [names]
    chosen: set[str] = set()
    for raw in names or ["DIST1"]:
        name = SIGNAL_ALIASES.get(str(raw).strip().upper(), str(raw).strip().upper())
        if name == "VIDEO":
            continue
        if name not in SIGNAL_ORDER:
            raise SourceError(f"unknown signal {raw!r}; use some of: {', '.join(SIGNAL_ORDER)}")
        chosen.add(name)
    if "DIST1" not in chosen:
        raise SourceError("source.signals must contain DIST1")
    return [name for name in SIGNAL_ORDER if name in chosen]


@dataclass(frozen=True)
class Block:
    article: int
    serial: int
    counter: int
    video_bytes: int
    frames: np.ndarray          # int32, shape (frames, values per frame)


class StreamParser:
    """Cuts the byte stream into blocks. It finds its way back after garbage or a half block."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self.skipped = 0
        self.length_per_frame = False       # the sensor gave the length of one frame instead of the whole block
        self.last_header: tuple[int, ...] | None = None

    def feed(self, data: bytes) -> list[Block]:
        self._buf += data
        blocks: list[Block] = []
        while True:
            start = self._buf.find(PREAMBLE_BYTES)
            if start < 0:
                keep = len(PREAMBLE_BYTES) - 1
                if len(self._buf) > keep:
                    self.skipped += len(self._buf) - keep
                    del self._buf[:-keep]
                break
            if start:
                self.skipped += start
                del self._buf[:start]
            if len(self._buf) < HEADER_SIZE:
                break
            fields = HEADER.unpack_from(self._buf, 0)
            _, article, serial, video, data_len, frames, counter = fields
            total_data = self._data_bytes(video, data_len, frames)
            if total_data is None:
                self.skipped += 1                   # these four bytes were not a header after all
                del self._buf[:1]
                continue
            total = HEADER_SIZE + video + total_data
            if len(self._buf) < total:
                break
            self.last_header = fields
            if total_data and frames:
                raw = bytes(self._buf[HEADER_SIZE + video:total])
                shaped = np.frombuffer(raw, dtype="<i4").reshape(frames, total_data // (4 * frames)).copy()
                blocks.append(Block(article, serial, counter, video, shaped))
            del self._buf[:total]
        return blocks

    def _data_bytes(self, video: int, data_len: int, frames: int) -> int | None:
        """Bytes of all frames of the block, or None when the header is not plausible.

        The manual calls the field "length measurement data". Normally that is the bytes of all frames together.
        Should a firmware give the bytes of one frame, that is recognised too, because the whole block would then
        be shorter than one value per frame."""
        if video > MAX_VIDEO_BYTES or frames > MAX_FRAMES or data_len % 4:
            return None
        if frames == 0:
            return 0 if data_len == 0 else None
        if data_len % (4 * frames) == 0:
            return data_len if data_len <= MAX_DATA_BYTES else None
        if data_len < 4 * frames and data_len * frames <= MAX_DATA_BYTES:
            self.length_per_frame = True
            return data_len * frames
        return None


def distances_mm(counts: np.ndarray) -> tuple[np.ndarray, int | None]:
    """Counts to millimetres. Status codes become NaN. The second result is the last status code, if any."""
    counts = np.asarray(counts, dtype=np.int64)
    bad = counts > LAST_VALID
    mm = np.where(bad, np.nan, counts / COUNTS_PER_MM)
    code = int(counts[bad][-1]) if bad.any() else None
    return mm, code


def friendly_error(exc: BaseException, host: str, port: int) -> str:
    where = f"{host}:{port}"
    if isinstance(exc, ConnectionRefusedError):
        return (f"The sensor at {where} refused the connection. Check that the Ethernet output is switched on "
                "(Server TCP/IP) and that the port is the one set in the sensor.")
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return (f"No answer from {where}. Check the IP address and the cable, and that this computer's Ethernet "
                "adapter is in the same network (for 169.254.168.150: 169.254.x.x, mask 255.255.0.0).")
    if isinstance(exc, socket.gaierror):
        return f"'{host}' is not a valid address."
    if isinstance(exc, OSError):
        no_route = getattr(exc, "winerror", None) in (10051, 10065) or exc.errno in (101, 113)
        if no_route:
            return (f"There is no route to {where}. Plug the cable into this computer and give its Ethernet "
                    "adapter an address in the sensor's network.")
    return f"Cannot reach {where}: {exc}"


class Ild5500Source(SensorSource):
    kind = "ild5500"

    def __init__(self, cfg: SensorConfig, rate_hz: int) -> None:
        super().__init__(cfg, rate_hz)
        opts = cfg.source
        self.host = str(opts.get("host", "169.254.168.150")).strip()
        try:
            self.port = int(opts.get("port", 1024))
        except (TypeError, ValueError) as exc:
            raise SourceError(f"{cfg.id}: source.port must be a number, got {opts.get('port')!r}") from exc
        if not self.host or not 1 <= self.port <= 65535:
            raise SourceError(f"{cfg.id}: source.host and source.port must be set (port 1..65535)")
        rate = opts.get("sensor_rate_hz", "auto")
        try:
            self.fixed_rate: float | None = None if str(rate).lower() == "auto" else float(rate)
        except ValueError as exc:
            raise SourceError(f"{cfg.id}: source.sensor_rate_hz must be auto or a number") from exc
        if self.fixed_rate is not None and self.fixed_rate <= 0:
            raise SourceError(f"{cfg.id}: source.sensor_rate_hz must be above 0")
        self.signals = normalise_signals(opts.get("signals"))
        self.invert = str(opts.get("invert", False)).lower() in ("1", "true", "yes", "on")
        try:
            self.range_mm = int(opts.get("range_mm", 10))
        except (TypeError, ValueError) as exc:
            raise SourceError(f"{cfg.id}: source.range_mm must be one of {list(MODEL_RANGES_MM)}") from exc
        ref = str(opts.get("reference_mm", "range_start")).strip().lower()
        self._config_ref: float | None = None
        self.ref_mode = ref if ref in ("auto", "mid", "range_start") else "fixed"
        if self.ref_mode == "fixed":
            try:
                self._config_ref = float(ref)
            except ValueError as exc:
                raise SourceError(f"{cfg.id}: source.reference_mm must be range_start, auto, mid or a number") from exc
        elif self.ref_mode == "range_start":
            self._config_ref = 0.0                      # DIST1 already counts from the start of the range
        elif self.ref_mode == "mid":
            if self.range_mm not in MODEL_RANGES_MM:
                raise SourceError(f"{cfg.id}: source.range_mm must be one of {list(MODEL_RANGES_MM)}")
            self._config_ref = self.range_mm / 2.0      # the middle of the range, counted from its start

        self._lock = threading.Lock()
        self._queue: deque[np.ndarray] = deque()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sock: socket.socket | None = None
        self._resampler = Resampler(rate_hz)
        self._reset_state()

    def _reset_state(self) -> None:
        with self._lock:
            self._queue.clear()
        self._resampler.reset()
        self._waiting_mm = np.empty(0)        # readings held back until the zero reference is known
        self._t_last: float | None = None
        self._arrivals: deque[tuple[float, int]] = deque()
        self._arrival_sum = 0
        self.ref_mm: float | None = self._config_ref
        # what the status shows
        self.state = "offline"
        self.message = f"Connecting to the sensor at {self.host}:{self.port}"
        self.article: int | None = None
        self.serial: int | None = None
        self.samples = 0
        self.invalid = 0
        self.last_status: str | None = None
        self._reasons: deque[tuple[float, dict[str, int]]] = deque()
        self.measured_rate: float | None = None
        self.intensity_percent: float | None = None
        self.temperature_c: float | None = None
        self.layout_error: str | None = None
        self.length_per_frame = False

    # -- lifecycle -------------------------------------------------------------------

    def start(self) -> None:
        self.stop()
        self._reset_state()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"ild5500-{self.cfg.id}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        sock = self._sock
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None

    # -- the reader thread -----------------------------------------------------------

    def _set(self, state: str, message: str) -> None:
        if (state, message) != (self.state, self.message):
            log.info("%s: %s", self.cfg.id, message)
        self.state, self.message = state, message

    def _run(self) -> None:
        delay = 0.5
        while not self._stop.is_set():
            try:
                self._set("offline", f"Connecting to the sensor at {self.host}:{self.port}")
                sock = socket.create_connection((self.host, self.port), timeout=3.0)
            except OSError as exc:
                self._set("offline", friendly_error(exc, self.host, self.port))
            else:
                self._sock = sock
                delay = 0.5
                try:
                    self._serve(sock)
                except OSError as exc:
                    if not self._stop.is_set():
                        self._set("offline", f"The connection to {self.host}:{self.port} was lost ({exc}). "
                                             "Trying again.")
                finally:
                    self._sock = None
                    try:
                        sock.close()
                    except OSError:
                        pass
            if self._stop.wait(delay):
                break
            delay = min(delay * 2, 4.0)

    def _serve(self, sock: socket.socket) -> None:
        sock.settimeout(0.5)
        parser = StreamParser()
        last_data = time.monotonic()
        self._set("no_data", f"Connected to {self.host}:{self.port}, waiting for the first readings.")
        while not self._stop.is_set():
            try:
                data = sock.recv(65536)
            except socket.timeout:
                if time.monotonic() - last_data > SILENCE_S:
                    self._set("no_data", "Connected, but the sensor sends no readings. In its web page, "
                                         "Settings > Outputs, switch the output to Ethernet.")
                continue
            if not data:
                raise OSError("closed by the sensor")
            last_data = time.monotonic()
            for block in parser.feed(data):
                self._on_block(block)
            self.length_per_frame = parser.length_per_frame

    def _column(self, name: str, words: int) -> int | None:
        if name not in self.signals or words != len(self.signals):
            return None
        return self.signals.index(name)

    def _on_block(self, block: Block) -> None:
        words = block.frames.shape[1]
        column = self._column("DIST1", words)
        if column is None:
            self.layout_error = (f"The sensor sends {words} values per reading but the configuration lists "
                                 f"{len(self.signals)} ({', '.join(self.signals)}). In the sensor's web page tick "
                                 "only DIST1 under the Ethernet data output.")
            self._set("no_data", self.layout_error)
            return
        self.layout_error = None
        self.article, self.serial = block.article, block.serial
        mm, code = distances_mm(block.frames[:, column])
        n = int(mm.size)
        now = time.monotonic()
        self.samples += n
        self.invalid += int(np.isnan(mm).sum())
        if code is not None:
            self.last_status = STATUS_TEXT.get(code, f"status code 0x{code:08X}")
            self._note_reasons(now, block.frames[:, column])
        self._extras(block.frames, words)
        self._note_arrival(now, n)
        share = float(np.isfinite(mm).mean())
        if share < 0.5 and self.last_status:
            self._set("invalid", f"Connected, but most readings are invalid: {self.last_status}.")
        else:
            self._set("streaming", f"Live from the sensor at {self.host}:{self.port}.")
        with self._lock:
            self._queue.append(mm)

    def _note_reasons(self, now: float, counts: np.ndarray) -> None:
        """Count the invalid readings of this block by reason, and forget what is older than the window."""
        raw = np.asarray(counts, dtype=np.int64)
        codes, found = np.unique(raw[raw > LAST_VALID], return_counts=True)
        tally: dict[str, int] = {}
        for code, number in zip(codes.tolist(), found.tolist()):
            key = STATUS_REASON.get(int(code), "other")
            tally[key] = tally.get(key, 0) + int(number)
        with self._lock:                            # the status is read from another thread
            self._reasons.append((now, tally))
            while self._reasons and now - self._reasons[0][0] > REASON_WINDOW_S:
                self._reasons.popleft()

    def invalid_reasons(self) -> dict[str, float] | None:
        """Share of each reason among the invalid readings of the last two seconds, or None when there were none."""
        horizon = time.monotonic() - REASON_WINDOW_S
        total: dict[str, int] = {}
        with self._lock:
            held = list(self._reasons)
        for stamp, tally in held:
            if stamp >= horizon:
                for key, number in tally.items():
                    total[key] = total.get(key, 0) + number
        everything = sum(total.values())
        if not everything:
            return None
        return {key: round(number / everything, 3) for key, number in total.items()}

    def _extras(self, frames: np.ndarray, words: int) -> None:
        """Optional values when ticked in the sensor: reflectance (INTENSITY) and temperature."""
        if words != len(self.signals):
            return
        last = frames[-1]
        if "INTENSITY" in self.signals:
            self.intensity_percent = round(float(last[self.signals.index("INTENSITY")]) / 4096.0 * 100.0, 1)
        if "TEMPERATURE" in self.signals:
            self.temperature_c = round(float(last[self.signals.index("TEMPERATURE")]) / 1000.0, 1)

    def _note_arrival(self, now: float, n: int) -> None:
        """Measure the readings per second that really arrive, from a sliding window of blocks."""
        self._arrivals.append((now, n))
        self._arrival_sum += n
        while len(self._arrivals) > 1 and now - self._arrivals[0][0] > RATE_WINDOW_S:
            self._arrival_sum -= self._arrivals.popleft()[1]
        first_time, first_count = self._arrivals[0]
        span = now - first_time
        if len(self._arrivals) > 1 and span >= 1.0:
            self.measured_rate = (self._arrival_sum - first_count) / span

    # -- the cockpit's side ----------------------------------------------------------

    @property
    def sensor_rate_hz(self) -> float | None:
        # what really arrives per second, not the measuring rate: the sensor may send every nth reading only
        return self.fixed_rate or self.measured_rate

    def read(self, now: float) -> tuple[np.ndarray, float]:
        with self._lock:
            chunks = list(self._queue)
            self._queue.clear()
        if chunks:
            self._waiting_mm = np.concatenate([self._waiting_mm, *chunks])
        out = self._convert()
        if out.size == 0:
            return np.empty(0), self._t_last or now
        self._t_last = now
        return out, now

    def _convert(self) -> np.ndarray:
        mm = self._waiting_mm
        rate = self.sensor_rate_hz
        if mm.size == 0 or not rate:
            return np.empty(0)
        if self.ref_mm is None:                       # reference_mm: auto, the first second is zero
            valid = np.isfinite(mm)
            have = int(valid.sum())
            if have >= max(50, int(rate * REFERENCE_S)) or (mm.size >= 2 * rate and have >= 50):
                self.ref_mm = float(np.median(mm[valid]))
                log.info("%s: zero reference %.4f mm (median of %d readings)", self.cfg.id, self.ref_mm, have)
            elif mm.size >= 2 * rate:
                # two seconds and still no usable reading: show them as invalid instead of waiting
                self._waiting_mm = np.empty(0)
                return self._resampler.push(np.full(mm.size, np.nan), rate)
            else:
                return np.empty(0)
        self._waiting_mm = np.empty(0)
        micrometres = (mm - self.ref_mm) * 1000.0
        if self.invert:
            micrometres = -micrometres
        return self._resampler.push(micrometres, rate)

    def status(self) -> dict[str, Any]:
        share = (self.invalid / self.samples) if self.samples else None
        rate = self.sensor_rate_hz
        return {
            "kind": self.kind, "state": self.state, "message": self.message,
            "host": self.host, "port": self.port,
            "rate_hz": round(rate) if rate else None,
            "article": self.article, "serial": self.serial, "samples": self.samples,
            "invalid_percent": round(share * 100, 1) if share is not None else None,
            "last_status": self.last_status,
            "invalid_reasons": self.invalid_reasons(),
            "reference": self.ref_mode,
            "reference_mm": round(self.ref_mm, 4) if self.ref_mm is not None else None,
            "intensity_percent": self.intensity_percent,
            "temperature_c": self.temperature_c,
        }

    def apply_setting(self, name: str, value: Any) -> None:
        log.info("%s: the setting %s=%r is not sent to a real sensor, change it in the sensor's web page",
                 self.cfg.id, name, value)
