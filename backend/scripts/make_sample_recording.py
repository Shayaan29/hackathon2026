"""Create a dummy CSV recording in sensorTOOL-like German format.

Use it to try the replay source before you have a real recording:

    python scripts/make_sample_recording.py
    # then in config/sensors.yaml for a sensor:
    #   source: {type: replay, path: data/sample_recording.csv, column: 1,
    #            file_rate_hz: 1000, scale: 1000.0, offset: 25000.0, decimal: ","}
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

RATE = 1000
SECONDS = 60
OUT = Path(__file__).resolve().parent.parent / "data" / "sample_recording.csv"


def main() -> None:
    rng = np.random.default_rng(7)
    t = np.arange(RATE * SECONDS) / RATE
    mm = 25.0 + rng.normal(0, 0.0008, t.size) + 0.0003 * np.sin(2 * math.pi * t / 40)
    tap = (t >= 20) & (t < 22)
    mm[tap] += 0.015 * np.exp(-(t[tap] - 20) / 0.35) * np.sin(2 * math.pi * 30 * (t[tap] - 20))
    mm[(t >= 40) & (rng.random(t.size) < 0.4) & (t < 42)] = np.nan
    lines = ["Zeit [s];Abstand [mm]"]
    for ti, vi in zip(t, mm):
        cell = "" if math.isnan(vi) else f"{vi:.6f}".replace(".", ",")
        lines.append(f"{ti:.3f}".replace(".", ",") + ";" + cell)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT} ({SECONDS} s at {RATE} Hz)")


if __name__ == "__main__":
    main()
