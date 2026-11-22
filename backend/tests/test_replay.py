import math

import numpy as np

from app.config import SensorConfig, Tolerance
from app.sources.replay import CsvReplaySource, load_csv_column


def write_german_csv(path, n=2000, rate=1000):
    lines = ["Zeit [s];Abstand [mm]"]
    for i in range(n):
        value = 25.0 + 0.001 * math.sin(i / 50)
        cell = "" if i == 10 else f"{value:.6f}".replace(".", ",")
        lines.append(f"{i / rate:.3f}".replace(".", ",") + ";" + cell)
    path.write_text("\n".join(lines), encoding="utf-8")


def test_reads_german_csv_with_header(tmp_path):
    path = tmp_path / "rec.csv"
    write_german_csv(path)
    data = load_csv_column(path, "Abstand [mm]", decimal=",")
    assert data.size == 2000
    assert math.isnan(data[10])
    assert abs(np.nanmean(data) - 25.0) < 0.01


def test_replay_resamples_scales_and_loops(tmp_path):
    path = tmp_path / "rec.csv"
    write_german_csv(path)
    cfg = SensorConfig(id="s1", name="Sensor 1", model="x", unit="µm", nominal=0.0,
                       tolerance=Tolerance(-40, 40), fixture="a",
                       source={"type": "replay", "path": str(path), "column": 1,
                               "file_rate_hz": 1000, "scale": 1000.0, "offset": 25000.0,
                               "decimal": ","},
                       settings={})
    source = CsvReplaySource(cfg, rate_hz=500)
    source.start()
    assert source._data.size == 1000  # 2 s at 500 Hz
    values, _ = source.read(100.0)
    assert values.size == 0
    values, _ = source.read(103.0)  # 3 s -> loops once
    assert values.size == 1500
    assert np.nanmax(np.abs(values)) < 1.5  # scaled to µm around the reference
