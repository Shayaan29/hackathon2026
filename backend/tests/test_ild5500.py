"""The live optoNCDT 5500 source, tested against a fake sensor that speaks its TCP format."""

from __future__ import annotations

import socket
import struct
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND / "scripts"))

import fake_ild5500  # noqa: E402
import sensor_check  # noqa: E402
from app.config import ConfigError, SensorConfig, Tolerance, expand_env, load_settings  # noqa: E402
from app.sources import SourceError, create_source  # noqa: E402
from app.sources.ild5500 import (HEADER, PREAMBLE, Ild5500Source, StreamParser, distances_mm,  # noqa: E402
                                 normalise_signals)
from app.sources.resample import Resampler  # noqa: E402

NO_PEAK = 0x7FFFFF04


def block_bytes(values: np.ndarray, article: int = 5500, serial: int = 7, counter: int = 0, video: bytes = b"",
                length_per_frame: bool = False) -> bytes:
    """One measured value block; values has shape (frames,) or (frames, words)."""
    frames = np.asarray(values, dtype="<i4")
    if frames.ndim == 1:
        frames = frames[:, None]
    payload = frames.tobytes()
    length = frames.shape[1] * 4 if length_per_frame else len(payload)
    return HEADER.pack(PREAMBLE, article, serial, len(video), length, frames.shape[0], counter) + video + payload


def ild_config(port: int, **source: object) -> SensorConfig:
    options = {"type": "ild5500", "host": "127.0.0.1", "port": port, "reference_mm": "auto", **source}
    return config_with(options)


def config_with(options: dict[str, object]) -> SensorConfig:
    return SensorConfig(id="s1", name="ILD5500", model="optoNCDT ILD5500-50", unit="µm", nominal=0.0,
                        tolerance=Tolerance(-50.0, 50.0), fixture="fixture-a", source=options, settings={})


class FakeSensor:
    """Serves the Ethernet stream on a free local port, like the sensor in "Server TCP/IP" mode."""

    def __init__(self, rate: float = 5000.0, scenario: str = "quiet", signals: tuple[str, ...] = ("DIST1",),
                 noise_um: float = 0.5, drop_after_s: float | None = None, garbage: bytes = b"",
                 silent: bool = False, frames: int = 100) -> None:
        self.rate, self.scenario, self.signals = rate, scenario, list(signals)
        self.noise_um, self.drop_after_s, self.garbage, self.silent, self.frames = noise_um, drop_after_s, garbage, silent, frames
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.server.settimeout(0.2)
        self.port = self.server.getsockname()[1]
        self.connections = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        rng = np.random.default_rng(3)
        while not self._stop.is_set():
            try:
                conn, _ = self.server.accept()
            except (socket.timeout, OSError):
                continue
            self.connections += 1
            sent, counter, start = 0, 0, time.monotonic()
            try:
                if self.garbage:
                    conn.sendall(self.garbage)
                while not self._stop.is_set():
                    if self.silent:
                        time.sleep(0.05)
                        continue
                    elapsed = time.monotonic() - start
                    if self.drop_after_s is not None and elapsed > self.drop_after_s:
                        break
                    n = min(int(elapsed * self.rate) - sent, self.frames)
                    if n < self.frames // 2:
                        time.sleep(0.002)
                        continue
                    conn.sendall(fake_ild5500.make_block(sent, n, self.rate, self.signals, self.scenario, 70.0,
                                                         self.noise_um, rng, 99, counter))
                    sent += n
                    counter += n
            except OSError:
                pass
            finally:
                conn.close()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self.server.close()


@pytest.fixture()
def sensor():
    made: list[FakeSensor] = []

    def factory(**kwargs: object) -> FakeSensor:
        fake = FakeSensor(**kwargs)  # type: ignore[arg-type]
        made.append(fake)
        return fake

    yield factory
    for fake in made:
        fake.close()


def collect(source: Ild5500Source, seconds: float) -> tuple[np.ndarray, float]:
    """Poll the source like the engine does (every 50 ms) and return everything it delivered."""
    out: list[np.ndarray] = []
    t0 = time.time()
    while time.time() - t0 < seconds:
        values, _ = source.read(time.time())
        out.append(values)
        time.sleep(0.05)
    return np.concatenate(out) if out else np.empty(0), time.time() - t0


# -- the byte stream ---------------------------------------------------------------------------------------------


def test_a_block_is_read_with_its_values_and_header_fields() -> None:
    counts = np.array([70_000_000, 70_000_123, 69_999_900])
    blocks = StreamParser().feed(block_bytes(counts, article=5500, serial=1234, counter=42))
    assert len(blocks) == 1
    block = blocks[0]
    assert (block.article, block.serial, block.counter) == (5500, 1234, 42)
    assert block.frames.shape == (3, 1)
    assert list(block.frames[:, 0]) == list(counts)


def test_the_stream_may_be_cut_anywhere() -> None:
    counts = [np.arange(i, i + 40) * 1000 + 70_000_000 for i in range(5)]
    stream = b"".join(block_bytes(c, counter=i * 40) for i, c in enumerate(counts))
    for step in (1, 3, 7, 28, 29, 1000):
        parser = StreamParser()
        got: list[np.ndarray] = []
        for i in range(0, len(stream), step):
            got.extend(b.frames[:, 0] for b in parser.feed(stream[i:i + step]))
        assert np.array_equal(np.concatenate(got), np.concatenate(counts)), f"step {step}"


def test_the_parser_finds_its_way_back_after_garbage() -> None:
    good = block_bytes(np.arange(10) + 70_000_000)
    fake_header = struct.pack("<I", PREAMBLE) + b"\xff" * 24      # looks like a header, is not one
    parser = StreamParser()
    blocks = parser.feed(b"\x00\x01garbage" + fake_header + good + b"xx" + good)
    assert [b.frames.shape[0] for b in blocks] == [10, 10]
    assert parser.skipped > 0


def test_video_data_is_skipped() -> None:
    video = bytes(range(256)) * 8
    blocks = StreamParser().feed(block_bytes(np.array([70_000_000, 70_000_001]), video=video))
    assert [list(b.frames[:, 0]) for b in blocks] == [[70_000_000, 70_000_001]]


def test_a_firmware_that_gives_the_length_of_one_frame_is_understood_too() -> None:
    parser = StreamParser()
    counts = np.arange(8) + 70_000_000
    blocks = parser.feed(block_bytes(counts, length_per_frame=True))
    assert len(blocks) == 1 and list(blocks[0].frames[:, 0]) == list(counts)
    assert parser.length_per_frame


def test_several_values_per_frame_keep_their_columns() -> None:
    frames = np.column_stack([np.arange(5) * 1000, np.full(5, 2300), np.arange(5) + 70_000_000])
    block = StreamParser().feed(block_bytes(frames))[0]
    assert block.frames.shape == (5, 3)
    assert list(block.frames[:, 2]) == list(frames[:, 2])


def test_status_codes_become_invalid_readings() -> None:
    mm, code = distances_mm(np.array([70_000_000, NO_PEAK, -1_500_000, 0x7FFFFEFF, 0x7FFFFF0A]))
    assert mm[0] == pytest.approx(70.0) and mm[2] == pytest.approx(-1.5)
    assert np.isnan(mm[1]) and np.isnan(mm[4])
    assert mm[3] == pytest.approx(0x7FFFFEFF / 1e6)           # the largest valid value
    assert code == 0x7FFFFF0A


def test_ticked_signals_are_put_in_the_order_the_sensor_sends_them() -> None:
    assert normalise_signals(["DIST1"]) == ["DIST1"]
    assert normalise_signals(["temperature", "dist1", "INTENSITY"]) == ["INTENSITY", "DIST1", "TEMPERATURE"]
    assert normalise_signals(["MEASTATE", "DIST1", "VIDEO"]) == ["MEASRATE", "DIST1"]
    with pytest.raises(SourceError):
        normalise_signals(["INTENSITY"])
    with pytest.raises(SourceError):
        normalise_signals(["DIST1", "BANANA"])


# -- bringing 20 kHz down to the analysis rate -----------------------------------------------------------------------


def test_blocks_are_averaged_and_a_mostly_invalid_block_is_invalid() -> None:
    values = np.arange(80, dtype=float)
    values[40:70] = np.nan                                      # 30 of 40 readings invalid in the second block
    out = Resampler(500).push(values, 20_000)
    assert out.size == 2
    assert out[0] == pytest.approx(19.5) and np.isnan(out[1])


@pytest.mark.parametrize("sensor_hz", [20_000, 3_330, 4_000, 1_500, 750, 19_873.4])
def test_the_output_rate_is_exact_whatever_the_ratio_and_however_the_data_is_chunked(sensor_hz: float) -> None:
    rng = np.random.default_rng(1)
    total = int(sensor_hz * 30)
    data = np.sin(np.arange(total) / sensor_hz * 2 * np.pi * 3.0)
    one_shot = Resampler(500).push(data, sensor_hz)
    chunked = Resampler(500)
    pieces = []
    pos = 0
    while pos < total:
        step = int(rng.integers(1, int(sensor_hz / 5) + 2))
        pieces.append(chunked.push(data[pos:pos + step], sensor_hz))
        pos += step
    out = np.concatenate(pieces)
    expected = total / (sensor_hz / 500)
    assert abs(one_shot.size - expected) <= 1 and abs(out.size - expected) <= 1       # no drift over 30 s
    n = min(one_shot.size, out.size)
    assert np.allclose(one_shot[:n], out[:n], atol=1e-6)                              # same result in chunks


def test_a_slow_sensor_is_held_to_the_analysis_rate() -> None:
    out = Resampler(500).push(np.array([1.0, 2.0, 3.0]), 250)
    assert list(out) == [1.0, 1.0, 2.0, 2.0, 3.0, 3.0]


def test_a_ramp_is_averaged_over_the_right_readings() -> None:
    out = Resampler(500).push(np.arange(100.0), 2_500)                       # ratio 5
    assert list(out) == [2.0, 7.0, 12.0, 17.0, 22.0, 27.0, 32.0, 37.0, 42.0, 47.0, 52.0, 57.0, 62.0, 67.0, 72.0,
                         77.0, 82.0, 87.0, 92.0, 97.0]


# -- the source against the fake sensor ------------------------------------------------------------------------------


def test_the_source_delivers_the_analysis_rate_with_zero_at_the_start(sensor) -> None:
    fake = sensor(rate=5000.0, noise_um=0.5)
    source = create_source(ild_config(fake.port), 500)
    assert isinstance(source, Ild5500Source)
    source.start()
    try:
        values, elapsed = collect(source, 4.0)
        status = source.status()
    finally:
        source.stop()
    assert status["state"] == "streaming" and status["article"] == 5500 and status["serial"] == 99
    assert status["rate_hz"] == pytest.approx(5000, rel=0.03)
    assert status["reference_mm"] == pytest.approx(70.0, abs=0.01)
    assert 500 * (elapsed - 2.0) * 0.8 < values.size < 500 * elapsed * 1.05           # one second to zero, then 500 Hz
    assert abs(np.nanmean(values)) < 2.5                                          # zero at the start, runout only
    assert np.isfinite(values).all()


def test_the_zero_can_be_a_fixed_distance_and_the_sign_can_be_turned(sensor) -> None:
    fake = sensor(rate=5000.0, noise_um=0.2)
    source = create_source(ild_config(fake.port, reference_mm=69.99, invert=True), 500)
    source.start()
    try:
        values, _ = collect(source, 2.5)
    finally:
        source.stop()
    assert values.size > 300
    assert np.nanmean(values) == pytest.approx(-10.0, abs=2.0)                     # 70.000 mm is 10 µm beyond 69.99


def test_no_target_is_delivered_as_invalid_readings_with_a_reason(sensor) -> None:
    fake = sensor(rate=2000.0, scenario="no-target")
    source = create_source(ild_config(fake.port, sensor_rate_hz=2000), 500)
    source.start()
    try:
        values, _ = collect(source, 3.5)
        status = source.status()
    finally:
        source.stop()
    assert values.size > 100 and np.isnan(values).all()
    assert status["state"] == "invalid" and status["invalid_percent"] == 100.0
    assert "no target" in status["message"] and "no peak" in status["last_status"]


def test_a_missing_reference_does_not_hold_back_good_readings_for_ever() -> None:
    source = Ild5500Source(ild_config(1), 500)
    source.measured_rate = 1000.0
    source._waiting_mm = np.full(500, 70.0)                        # half a second: wait
    assert source._convert().size == 0
    source._waiting_mm = np.concatenate([np.full(1000, 70.0), np.full(500, 70.001)])
    out = source._convert()                                        # more than a second of valid readings
    assert out.size == 750 and source.ref_mm == pytest.approx(70.0)
    source = Ild5500Source(ild_config(1), 500)
    source.measured_rate = 1000.0
    source._waiting_mm = np.full(2500, np.nan)                     # two seconds without any usable reading
    out = source._convert()
    assert out.size == 1250 and np.isnan(out).all() and source.ref_mm is None


def test_a_sensor_that_is_not_there_is_reported_in_plain_words() -> None:
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()                                                   # nothing listens here any more
    source = create_source(ild_config(port), 500)
    source.start()
    try:
        deadline = time.time() + 5
        while time.time() < deadline and "refused" not in source.status()["message"]:
            time.sleep(0.1)
        status = source.status()
    finally:
        source.stop()
    assert status["state"] == "offline" and "refused the connection" in status["message"]
    assert "Server TCP/IP" in status["message"]
    values, _ = source.read(time.time())
    assert values.size == 0


def test_a_sensor_that_connects_but_stays_quiet_is_reported(sensor, monkeypatch) -> None:
    monkeypatch.setattr("app.sources.ild5500.SILENCE_S", 0.6)
    fake = sensor(silent=True)
    source = create_source(ild_config(fake.port), 500)
    source.start()
    try:
        deadline = time.time() + 5
        while time.time() < deadline and "sends no readings" not in source.status()["message"]:
            time.sleep(0.1)
        message = source.status()["message"]
    finally:
        source.stop()
    assert "sends no readings" in message and "Ethernet" in message


def test_more_ticked_boxes_than_configured_are_explained(sensor) -> None:
    fake = sensor(signals=("INTENSITY", "DIST1"))
    source = create_source(ild_config(fake.port), 500)               # the config lists DIST1 only
    source.start()
    try:
        deadline = time.time() + 4
        while time.time() < deadline and "tick only DIST1" not in source.status()["message"]:
            time.sleep(0.1)
        status = source.status()
    finally:
        source.stop()
    assert status["state"] == "no_data" and "2 values per reading" in status["message"]


def test_extra_values_are_shown_when_they_are_ticked(sensor) -> None:
    fake = sensor(signals=("INTENSITY", "DIST1", "TEMPERATURE"), rate=2000.0)
    source = create_source(ild_config(fake.port, signals=["TEMPERATURE", "DIST1", "INTENSITY"]), 500)
    source.start()
    try:
        values, _ = collect(source, 2.5)
        status = source.status()
    finally:
        source.stop()
    assert status["intensity_percent"] == pytest.approx(56.2, abs=0.1)      # 2300 / 4096
    assert status["temperature_c"] == pytest.approx(31.4, abs=1.0)
    assert values.size > 200


def test_the_source_connects_again_after_the_sensor_drops_the_connection(sensor) -> None:
    fake = sensor(rate=2000.0, drop_after_s=1.0)
    source = create_source(ild_config(fake.port), 500)
    source.start()
    try:
        deadline = time.time() + 8
        while time.time() < deadline and fake.connections < 2:
            time.sleep(0.1)
    finally:
        source.stop()
    assert fake.connections >= 2


def test_stop_returns_quickly_and_start_can_follow(sensor) -> None:
    fake = sensor(rate=2000.0)
    source = create_source(ild_config(fake.port), 500)
    source.start()
    time.sleep(0.5)
    began = time.time()
    source.stop()
    assert time.time() - began < 2.0
    source.start()
    time.sleep(0.5)
    assert source.status()["state"] in ("streaming", "no_data")
    source.stop()


# -- configuration ---------------------------------------------------------------------------------------------------


def test_bad_options_are_named() -> None:
    for options, word in [({"port": "abc"}, "port"), ({"port": 70000}, "port"), ({"signals": ["INTENSITY"]}, "DIST1"),
                          ({"reference_mm": "banana"}, "reference_mm"), ({"sensor_rate_hz": "fast"}, "sensor_rate_hz"),
                          ({"reference_mm": "mid", "range_mm": 33}, "range_mm")]:
        with pytest.raises(SourceError) as info:
            Ild5500Source(config_with({"type": "ild5500", "host": "127.0.0.1", "port": 1024, **options}), 500)
        assert word in str(info.value)


def test_the_middle_of_the_measuring_range_is_a_choice() -> None:
    assert Ild5500Source(ild_config(1, reference_mm="mid", range_mm=50), 500).ref_mm == 70.0
    assert Ild5500Source(ild_config(1, reference_mm="mid", range_mm=10), 500).ref_mm == 35.0
    assert Ild5500Source(ild_config(1, reference_mm="mid", range_mm=200), 500).ref_mm == 200.0


def test_addresses_come_from_the_env_file_or_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIVE_SENSOR_HOST", raising=False)
    assert expand_env("${LIVE_SENSOR_HOST:-169.254.168.150}", "x") == "169.254.168.150"
    monkeypatch.setenv("LIVE_SENSOR_HOST", "10.1.2.3")
    assert expand_env("${LIVE_SENSOR_HOST:-169.254.168.150}", "x") == "10.1.2.3"
    monkeypatch.setenv("LIVE_SENSOR_HOST", "  ")            # an empty line copied from .env.example
    assert expand_env("${LIVE_SENSOR_HOST:-169.254.168.150}", "x") == "169.254.168.150"
    assert expand_env("no variable here", "x") == "no variable here"
    with pytest.raises(ConfigError):
        expand_env("${NOT_SET_ANYWHERE}", "sensors[0].source.host")


def test_the_live_sensor_file_loads_and_can_be_forced_back_to_dummy_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIVE_SENSOR_HOST", raising=False)
    monkeypatch.delenv("LIVE_SENSOR_PORT", raising=False)
    monkeypatch.setenv("SOURCE_OVERRIDE", "")
    settings = load_settings(BACKEND / "config" / "sensors.live.yaml")
    assert [s.source_type for s in settings.sensors] == ["ild5500"]
    source = create_source(settings.sensors[0], settings.engine.analysis_rate_hz)
    assert (source.host, source.port) == ("169.254.168.150", 1024)          # type: ignore[attr-defined]
    monkeypatch.setenv("LIVE_SENSOR_HOST", "192.168.7.7")
    monkeypatch.setenv("LIVE_SENSOR_PORT", "5000")
    settings = load_settings(BACKEND / "config" / "sensors.live.yaml")
    source = create_source(settings.sensors[0], settings.engine.analysis_rate_hz)
    assert (source.host, source.port) == ("192.168.7.7", 5000)              # type: ignore[attr-defined]
    monkeypatch.setenv("SOURCE_OVERRIDE", "simulator")
    assert load_settings(BACKEND / "config" / "sensors.live.yaml").sensors[0].source_type == "simulator"


# -- the check script ------------------------------------------------------------------------------------------------


def run_check(port: int, seconds: float = 1.5) -> tuple[bool, str]:
    lines: list[str] = []
    ok = sensor_check.run_check("127.0.0.1", port, seconds, ["DIST1"], say=lines.append)
    return ok, "\n".join(lines)


def test_the_check_script_says_ok_for_a_working_sensor(sensor) -> None:
    ok, text = run_check(sensor(rate=5000.0).port)
    assert ok and "OK. The sensor delivers measurements" in text
    assert "article 5500" in text and "readings per second" in text


def test_the_check_script_explains_a_closed_port() -> None:
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()
    ok, text = run_check(port)
    assert not ok and "Server TCP/IP" in text and "Output interface" in text


def test_the_check_script_explains_a_quiet_sensor(sensor) -> None:
    ok, text = run_check(sensor(silent=True).port, seconds=1.0)
    assert not ok and "SENDS NOTHING" in text and "tick Ethernet" in text


def test_the_check_script_shows_unknown_data(sensor) -> None:
    ok, text = run_check(sensor(silent=True, garbage=b"HTTP/1.1 400 Bad Request\r\n\r\n").port, seconds=1.0)
    assert not ok and "not in the expected format" in text and "48 54 54 50" in text      # the bytes of "HTTP"


def test_the_check_script_explains_a_target_that_is_missing(sensor) -> None:
    ok, text = run_check(sensor(rate=2000.0, scenario="no-target").port)
    assert ok and "NO GOOD MEASUREMENT" in text and "no peak" in text
