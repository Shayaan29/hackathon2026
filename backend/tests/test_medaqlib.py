"""The MEDAQLib source with a stand-in for the DLL: the sensor finder, the frame layout and the readings."""

from __future__ import annotations

import dataclasses
import time

import numpy as np
import pytest

from app.sources import create_source
from app.sources.medaqlib import SENSOR_TYPES, MedaqlibSource

DBL_MAX = 1.79769e308


class FakeMedaq:
    """Answers like MEDAQLib with one ILD5500 on Ethernet that sends distance and intensity at 1 kHz."""

    def __init__(self, path=None, present=("ILD5500",), interface="TCP/IP", per_frame=2):
        self.path = path
        self.present = {SENSOR_TYPES[m] for m in present}
        self.interface = interface
        self.per_frame = per_frame
        self.handles: dict[int, dict] = {}
        self.next = 1
        self.opened: dict | None = None
        self.searched: list[int] = []
        self.started = 0.0
        self.sent = 0

    def create(self, sensor_type):
        handle = self.next
        self.next += 1
        self.handles[handle] = {"type": sensor_type, "params": {}, "progress": 0.0}
        return handle

    def release(self, handle):
        self.handles.pop(handle, None)
        return 0

    def set_int(self, handle, name, value):
        self.handles[handle]["params"][name] = value
        return 0

    def set_str(self, handle, name, value):
        self.handles[handle]["params"][name] = value
        return 0

    def get_int(self, handle, name):
        h = self.handles[handle]
        if name == "IA_Found":
            return 0, 1 if h["type"] in self.present else 0
        if name == "IP_SensorType":
            return 0, h["type"]
        if name == "IP_Baudrate":
            return 0, 0
        if name == "IA_ValuesPerFrame":
            return 0, self.per_frame
        return -1, 0

    def get_str(self, handle, name):
        h = self.handles[handle]
        answers = {"IP_SensorTypeName": "ILD5500", "IP_Interface": self.interface,
                   "IP_RemoteAddr": "169.254.168.150", "IP_Port": "COM7", "SA_SerialNumber": "01425060077",
                   "IA_Scaled_Name1": "Distance1", "IA_Scaled_Unit1": "mm",
                   "IA_Scaled_Name2": "Intensity1", "IA_Scaled_Unit2": "%"}
        if name in answers and (h["type"] in self.present or name.startswith("IA_Scaled")):
            return 0, answers[name]
        return -1, ""

    def exec_cmd(self, handle, command):
        return 0

    def set_int_exec(self, handle, command, name, value):
        if command == "Start_FindSensor":
            self.searched.append(value)
        return 0

    def exec_get_double(self, handle, command, name):
        h = self.handles[handle]
        h["progress"] = min(100.0, h["progress"] + 50.0)
        return 0, h["progress"]

    def open(self, handle):
        self.opened = dict(self.handles[handle]["params"], type=self.handles[handle]["type"])
        self.started = time.monotonic()
        self.sent = 0
        return 0

    def close(self, handle):
        return 0

    def _due(self):
        return int((time.monotonic() - self.started) * 1000) - self.sent

    def data_avail(self, handle):
        return 0, max(self._due(), 0) * self.per_frame

    def transfer(self, handle, max_values):
        frames = min(max_values // self.per_frame, max(self._due(), 0))
        k = self.sent + np.arange(frames)
        distance = 6.5 + 0.001 * np.sin(k / 50.0)
        distance[k % 10 == 0] = -DBL_MAX                    # every tenth reading cannot be scaled
        values = np.stack([distance, np.full(frames, 55.0)], axis=1)[:, : self.per_frame]
        self.sent += frames
        return 0, values.reshape(-1).astype(float)

    def error_text(self, handle):
        return "fake error"


def medaq_cfg(settings, **source):
    cfg = settings.sensors[0]
    return dataclasses.replace(cfg, source={"type": "medaqlib", **source})


def wait_for(predicate, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture()
def dll(tmp_path):
    path = tmp_path / "MEDAQLib.dll"
    path.write_bytes(b"stand-in")
    return path


def test_it_finds_the_sensor_opens_it_and_streams_micrometres(settings, dll):
    fake = FakeMedaq()
    source = MedaqlibSource(medaq_cfg(settings, dll_path=str(dll)), 500, library=lambda path: fake)
    source.start()
    try:
        assert wait_for(lambda: source.status()["state"] == "streaming"), source.status()
        status = source.status()
        assert status["model"] == "ILD5500" and status["interface"] == "TCP/IP"
        assert status["host"] == "ILD5500 via TCP/IP 169.254.168.150" and status["serial"] == 1425060077
        assert fake.searched[0] == SENSOR_TYPES["ILD5500"]
        assert fake.opened["IP_Interface"] == "TCP/IP" and fake.opened["IP_RemoteAddr"] == "169.254.168.150"
        assert fake.opened["IP_AutomaticMode"] == 3
        assert wait_for(lambda: source.status()["rate_hz"] is not None)
        time.sleep(0.6)
        values, _ = source.read(time.time())
        assert values.size > 50
        valid = values[np.isfinite(values)]
        assert np.allclose(valid, 6500.0, atol=2.0)            # mm from the start of the range, in µm
        assert source.status()["invalid_percent"] == pytest.approx(10.0, abs=1.5)
    finally:
        source.stop()


def test_without_a_sensor_it_says_so_and_keeps_looking(settings, dll):
    fake = FakeMedaq(present=())
    source = MedaqlibSource(medaq_cfg(settings, dll_path=str(dll), sensor="ILD1420"), 500, library=lambda p: fake)
    source.start()
    try:
        assert wait_for(lambda: "No Micro-Epsilon sensor found" in source.status()["message"])
        assert source.status()["state"] == "offline" and fake.searched == [SENSOR_TYPES["ILD1420"]]
    finally:
        source.stop()


def test_a_missing_dll_is_a_message_not_a_crash(settings, tmp_path):
    source = create_source(medaq_cfg(settings, dll_path=str(tmp_path / "nowhere.dll")), 500)
    source.start()
    try:
        assert wait_for(lambda: "MEDAQLib.dll was not found" in source.status()["message"])
        assert source.read(time.time())[0].size == 0
    finally:
        source.stop()


def test_a_configured_address_skips_the_search(settings, dll):
    fake = FakeMedaq(per_frame=1)
    source = MedaqlibSource(medaq_cfg(settings, dll_path=str(dll), sensor="ILD5500", address="192.168.0.20"), 500,
                            library=lambda p: fake)
    source.start()
    try:
        assert wait_for(lambda: source.status()["state"] == "streaming")
        assert fake.searched == [] and fake.opened["IP_RemoteAddr"] == "192.168.0.20"
    finally:
        source.stop()


def test_an_unknown_model_is_refused_at_start_up(settings, dll):
    with pytest.raises(Exception, match="source.sensor"):
        MedaqlibSource(medaq_cfg(settings, dll_path=str(dll), sensor="XYZ123"), 500)
