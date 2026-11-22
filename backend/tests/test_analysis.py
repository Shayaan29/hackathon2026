import math

import numpy as np

from app.analysis import (decimate_minmax, linear_trend, minutes_to_limit, robust_sigma,
                          spectral_peak, summary, to_json_list)
from app.buffer import RingBuffer, SensorBuffer

FS = 500


def test_spectral_peak_finds_sine():
    t = np.arange(FS * 2) / FS
    x = 5.0 * np.sin(2 * math.pi * 30 * t) + np.random.default_rng(1).normal(0, 0.5, t.size)
    peak = spectral_peak(x, FS)
    assert peak is not None
    assert abs(peak["frequency_hz"] - 30) < 0.5
    assert 4.0 < peak["amplitude"] < 6.0
    assert peak["prominence"] > 20


def test_robust_sigma_ignores_drift():
    rng = np.random.default_rng(2)
    x = rng.normal(0, 0.8, 5000) + np.linspace(0, 50, 5000)
    assert abs(robust_sigma(x) - 0.8) < 0.08


def test_linear_trend_and_forecast():
    t = np.arange(60.0)
    v = 0.1 * t + 2.0
    trend = linear_trend(t, v)
    assert abs(trend["slope_per_s"] - 0.1) < 1e-9
    assert trend["r2"] > 0.999
    assert minutes_to_limit(10.0, 6.0, -40, 40) == 5.0
    assert minutes_to_limit(-10.0, -6.0, -40, 40) == 5.0
    assert minutes_to_limit(10.0, -6.0, -40, 40) == (10.0 + 40.0) / 6.0
    assert minutes_to_limit(45.0, 1.0, -40, 40) == 0.0
    assert minutes_to_limit(10.0, 0.0, -40, 40) is None


def test_decimate_keeps_spike_and_size():
    x = np.zeros(100_000)
    x[54_321] = 99.0
    out, bucket = decimate_minmax(x, 1000)
    assert out.size <= 1000
    assert bucket > 1
    assert out.max() == 99.0


def test_decimate_handles_invalid_blocks():
    x = np.ones(1000)
    x[:500] = np.nan
    out, _ = decimate_minmax(x, 100)
    assert np.isnan(out[0])
    assert out[-1] == 1.0


def test_summary_and_json_conversion():
    x = np.array([1.0, np.nan, 3.0])
    s = summary(x)
    assert s["latest"] == 3.0 and abs(s["valid_ratio"] - 2 / 3) < 1e-9
    assert to_json_list(x) == [1.0, None, 3.0]


def test_ring_buffer_wraps_in_order():
    ring = RingBuffer(5)
    ring.extend(np.arange(3))
    ring.extend(np.arange(3, 8))
    assert ring.last(5).tolist() == [3, 4, 5, 6, 7]
    assert ring.last(2).tolist() == [6, 7]
    ring.extend(np.arange(100, 120))
    assert ring.last(3).tolist() == [117, 118, 119]


def test_sensor_buffer_aggregates_seconds():
    buf = SensorBuffer(100, 60, 600)
    start = 1000.0
    for i in range(1, 1001):  # 10 seconds in 10 ms steps
        buf.append(np.array([float(i)]), start + i / 100)
    slow = buf.slow_window(100)
    assert 8 <= slow["t"].size <= 10
    assert np.all(np.diff(slow["mean"]) > 0)
    t0, dt, values = buf.fast_window(2.0)
    assert values.size == 200 and dt == 0.01
    _, _, ranged = buf.fast_range(start + 5.0, start + 6.0)
    assert ranged.size == 100
    assert ranged[0] == 501 or ranged[0] == 502
