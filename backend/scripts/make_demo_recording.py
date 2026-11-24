"""Generate SIMULATED demo recordings in the messy formats a real export can have.

The files are for training and for testing the upload. They are labelled as
simulated in their own header, and the cockpit shows that label in every report.
Nothing here is a measurement of a real part.

    python scripts/make_demo_recording.py                 # writes data/demo_recording.csv
    python scripts/make_demo_recording.py --variant english_rate --out data/demo_plain.csv

Variants
    german          semicolons, decimal commas, "Zeit [s]" time column, units in the header
    english_rate    commas, decimal points, no time column (enter the rate), no units
    tab_mm_counter  tabs, decimal commas, a row counter, values in mm, no time column
    gaps            german, with "invalid" text and empty cells in the readings

The planted anomalies are listed in PLANTED. The tests check that the analysis finds them.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

SIMULATED_NOTE = "# SIMULATED demo data for the KI Sensor Cockpit. Generated for training, not a measurement."
MICRO = "µm"

PLANTED = [
    {"kind": "vibration", "sensor": 1, "from_s": 20.0, "to_s": 21.5},
    {"kind": "drift", "sensor": 2, "from_s": 50.0, "to_s": 110.0},
    {"kind": "noise", "sensor": 1, "from_s": 90.0, "to_s": 96.0},
    {"kind": "invalid", "sensor": 2, "from_s": 105.0, "to_s": 107.0},
    {"kind": "out_of_range", "sensor": 1, "from_s": 118.0, "to_s": 119.5},
]
VARIANTS = ("german", "english_rate", "tab_mm_counter", "gaps")


def signals(duration_s: float, rate_hz: int, seed: int = 7) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Two sensors in micrometres with the planted events."""
    rng = np.random.default_rng(seed)
    n = int(round(duration_s * rate_hz))
    t = np.arange(n) / rate_hz
    s1 = rng.normal(0.0, 0.8, n)
    s2 = rng.normal(0.0, 0.8, n)
    burst = (t >= 20.0) & (t < 21.5)  # 26 Hz vibration on sensor 1, echoed on sensor 2
    wave = 6.0 * np.sin(2 * np.pi * 26.0 * t)
    s1 += np.where(burst, wave, 0.0)
    s2 += np.where(burst, 0.8 * wave, 0.0)
    ramp = (t >= 50.0) & (t < 110.0)  # steady drift of 6 µm per minute on sensor 2
    s2 += np.where(ramp, 0.1 * (t - 50.0), 0.0)
    noisy = (t >= 90.0) & (t < 96.0)  # noise four times normal on sensor 1
    s1 += np.where(noisy, rng.normal(0.0, 3.2, n), 0.0)
    jump = (t >= 118.0) & (t < 119.5)  # sensor 1 leaves the tolerance band
    s1 += np.where(jump, 55.0, 0.0)
    gap = (t >= 105.0) & (t < 107.0)  # sensor 2 loses its target
    s2 = np.where(gap, np.nan, s2)
    return t, s1, s2


def _num(value: float, decimal: str, digits: int = 3) -> str:
    if not np.isfinite(value):
        return "invalid"
    text = f"{value:.{digits}f}"
    return text.replace(".", ",") if decimal == "," else text


def make_text(variant: str = "german", duration_s: float = 130.0, rate_hz: int = 1000, seed: int = 7) -> str:
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; use one of {', '.join(VARIANTS)}")
    t, s1, s2 = signals(duration_s, rate_hz, seed)
    if variant == "german":
        lines = [SIMULATED_NOTE, "# Device: DEMO optoNCDT (simulated)", "# Export: simulated German format",
                 f"Zeit [s];Sensor 1 [{MICRO}];Sensor 2 [{MICRO}]"]
        rows = (f"{_num(ti, ',', 3)};{_num(a, ',')};{_num(b, ',')}" for ti, a, b in zip(t, s1, s2))
        return "\n".join(lines) + "\n" + "\n".join(rows) + "\n"
    if variant == "gaps":
        lines = [SIMULATED_NOTE, f"Zeit [s];Sensor 1 [{MICRO}];Sensor 2 [{MICRO}]"]
        rows = []
        for i, (ti, a, b) in enumerate(zip(t, s1, s2)):
            cell_b = "" if (i % 9973 == 0) else _num(b, ',')  # an empty cell now and then
            rows.append(f"{_num(ti, ',', 3)};{_num(a, ',')};{cell_b}")
        return "\n".join(lines) + "\n" + "\n".join(rows) + "\n"
    if variant == "english_rate":
        lines = [SIMULATED_NOTE, "Sensor1,Sensor2"]
        rows = (f"{_num(a, '.')},{_num(b, '.')}" for a, b in zip(s1, s2))
        return "\n".join(lines) + "\n" + "\n".join(rows) + "\n"
    # tab_mm_counter: values in millimetres, a counter column, decimal commas
    lines = [SIMULATED_NOTE, "Nr\tSensor 1 [mm]\tSensor 2 [mm]"]
    rows = (f"{i + 1}\t{_num(a / 1000.0, ',', 6)}\t{_num(b / 1000.0, ',', 6)}"
            for i, (a, b) in enumerate(zip(s1, s2)))
    return "\n".join(lines) + "\n" + "\n".join(rows) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--variant", default="german", choices=VARIANTS)
    parser.add_argument("--out", default=None, help="output file (default: data/demo_recording.csv)")
    parser.add_argument("--duration", type=float, default=130.0)
    parser.add_argument("--rate", type=int, default=1000)
    args = parser.parse_args()
    backend = Path(__file__).resolve().parent.parent
    out = Path(args.out) if args.out else backend / "data" / "demo_recording.csv"
    if not out.is_absolute():
        out = backend / out
    out.parent.mkdir(parents=True, exist_ok=True)
    text = make_text(args.variant, args.duration, args.rate)
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB, variant {args.variant}, simulated)")


if __name__ == "__main__":
    main()
