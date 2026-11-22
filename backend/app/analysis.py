"""Signal analysis used by the alert rules, the chart endpoints and the AI tools.

All functions are pure: they take numpy arrays (NaN marks an invalid reading)
and return plain Python values that can go straight into JSON.
"""

from __future__ import annotations

import math
import warnings
from typing import Any

import numpy as np


def finite(values: np.ndarray) -> np.ndarray:
    return values[np.isfinite(values)]


def summary(values: np.ndarray) -> dict[str, Any]:
    """Basic statistics of a window, ignoring invalid readings."""
    n = int(values.size)
    ok = finite(values)
    if ok.size == 0:
        return {"n": n, "valid_ratio": 0.0, "latest": None, "mean": None,
                "min": None, "max": None, "std": None, "p2p": None}
    return {
        "n": n,
        "valid_ratio": float(ok.size / n) if n else 0.0,
        "latest": float(ok[-1]),
        "mean": float(ok.mean()),
        "min": float(ok.min()),
        "max": float(ok.max()),
        "std": float(ok.std()),
        "p2p": float(ok.max() - ok.min()),
    }


def robust_sigma(values: np.ndarray) -> float | None:
    """Noise level that ignores slow trends and short bursts.

    Uses the median absolute deviation of first differences, scaled so that
    it equals the standard deviation for white noise.
    """
    ok = finite(values)
    if ok.size < 16:
        return None
    diffs = np.diff(ok)
    mad = float(np.median(np.abs(diffs - np.median(diffs))))
    return 1.4826 * mad / math.sqrt(2.0)


def detrended_std(values: np.ndarray) -> float | None:
    """Standard deviation after removing a straight-line trend."""
    ok = finite(values)
    if ok.size < 16:
        return None
    x = np.arange(ok.size, dtype=float)
    slope, intercept = np.polyfit(x, ok, 1)
    return float(np.std(ok - (slope * x + intercept)))


def _fill_invalid(values: np.ndarray) -> np.ndarray:
    """Replace NaN by linear interpolation so FFTs stay meaningful."""
    mask = np.isfinite(values)
    if mask.all():
        return values.astype(float, copy=True)
    if not mask.any():
        return np.zeros_like(values, dtype=float)
    idx = np.arange(values.size)
    return np.interp(idx, idx[mask], values[mask])


def amplitude_spectrum(values: np.ndarray, fs: float) -> tuple[np.ndarray, np.ndarray]:
    """Single-sided amplitude spectrum: a sine of amplitude A shows a peak near A."""
    if values.size < 32:
        return np.array([]), np.array([])
    x = _fill_invalid(values)
    idx = np.arange(x.size, dtype=float)
    slope, intercept = np.polyfit(idx, x, 1)
    x = x - (slope * idx + intercept)
    window = np.hanning(x.size)
    spectrum = np.fft.rfft(x * window)
    amps = 2.0 * np.abs(spectrum) / window.sum()
    freqs = np.fft.rfftfreq(x.size, d=1.0 / fs)
    amps[0] = 0.0
    return freqs, amps


def spectral_peak(values: np.ndarray, fs: float, fmin: float = 2.0,
                  fmax: float | None = None) -> dict[str, float] | None:
    """Strongest frequency in a window and how clearly it stands out."""
    freqs, amps = amplitude_spectrum(values, fs)
    if freqs.size == 0:
        return None
    upper = fmax if fmax is not None else fs / 2.0
    band = (freqs >= fmin) & (freqs <= upper)
    if band.sum() < 3:
        return None
    band_idx = np.flatnonzero(band)
    i = int(band_idx[np.argmax(amps[band])])
    peak = float(amps[i])
    floor = float(np.median(amps[band])) or 1e-12
    freq = float(freqs[i])
    # Parabolic interpolation for a sharper frequency estimate.
    if 0 < i < amps.size - 1:
        a, b, c = amps[i - 1], amps[i], amps[i + 1]
        denom = a - 2 * b + c
        if denom != 0:
            shift = 0.5 * (a - c) / denom
            if abs(shift) < 1:
                freq += shift * (freqs[1] - freqs[0])
    return {"frequency_hz": freq, "amplitude": peak, "prominence": peak / floor}


def linear_trend(t: np.ndarray, v: np.ndarray) -> dict[str, float] | None:
    """Least-squares line through (t, v) with its R²."""
    mask = np.isfinite(t) & np.isfinite(v)
    if mask.sum() < 3:
        return None
    tt, vv = t[mask], v[mask]
    if np.ptp(tt) == 0:
        return None
    slope, intercept = np.polyfit(tt, vv, 1)
    predicted = slope * tt + intercept
    ss_res = float(np.sum((vv - predicted) ** 2))
    ss_tot = float(np.sum((vv - vv.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return {"slope_per_s": float(slope), "intercept": float(intercept), "r2": float(max(r2, 0.0))}


def best_trend(t: np.ndarray, v: np.ndarray, windows: tuple[float, ...], min_r2: float,
               min_rate_per_min: float = 0.0, t_end: float | None = None) -> dict[str, float] | None:
    """Steepest straight-line trend among several look-back windows.

    Short windows react fast to a drift that just started; long windows give a
    steadier rate once it has been going for a while. Windows that are not
    straight enough (R² below ``min_r2``) or too flat are ignored.
    """
    if t.size < 3:
        return None
    end = float(t[-1]) if t_end is None else float(t_end)
    best: dict[str, float] | None = None
    for window in windows:
        mask = t >= end - window
        if mask.sum() < max(8, int(window * 0.6)):
            continue
        trend = linear_trend(t[mask], v[mask])
        if trend is None:
            continue
        rate = trend["slope_per_s"] * 60.0
        if trend["r2"] < min_r2 or abs(rate) < min_rate_per_min:
            continue
        candidate = {**trend, "rate_per_min": rate, "window_s": float(window),
                     "current": trend["slope_per_s"] * end + trend["intercept"]}
        if best is None or abs(rate) > abs(best["rate_per_min"]):
            best = candidate
    return best


def minutes_to_limit(current: float | None, rate_per_min: float | None,
                     low: float, high: float) -> float | None:
    """Minutes until a straight-line trend crosses the tolerance band.

    Returns 0 when already outside in the direction of travel and None when the
    trend is flat or moving back towards the middle of the band.
    """
    if current is None or rate_per_min is None or abs(rate_per_min) < 1e-9:
        return None
    if rate_per_min > 0:
        if current >= high:
            return 0.0
        return (high - current) / rate_per_min
    if current <= low:
        return 0.0
    return (current - low) / (-rate_per_min)


def decimate_minmax(values: np.ndarray, max_points: int) -> tuple[np.ndarray, int]:
    """Downsample while keeping peaks visible.

    Each bucket contributes its minimum and maximum in the order they occurred,
    so a short spike survives even when thousands of samples become hundreds of
    points. Returns the new values and the bucket size in samples. The newest
    samples are always kept; leftovers are dropped from the old end.
    """
    n = int(values.size)
    max_points = max(int(max_points), 4)
    if n <= max_points:
        return values.astype(float, copy=True), 1
    bucket = int(math.ceil(n / (max_points / 2)))
    m = n // bucket
    block = values[n - m * bucket:].reshape(m, bucket)
    nan_rows = ~np.isfinite(block).any(axis=1)
    lo_src = np.where(np.isfinite(block), block, np.inf)
    hi_src = np.where(np.isfinite(block), block, -np.inf)
    i_min = lo_src.argmin(axis=1)
    i_max = hi_src.argmax(axis=1)
    rows = np.arange(m)
    v_min = block[rows, i_min].astype(float)
    v_max = block[rows, i_max].astype(float)
    v_min[nan_rows] = np.nan
    v_max[nan_rows] = np.nan
    first = np.where(i_min <= i_max, v_min, v_max)
    second = np.where(i_min <= i_max, v_max, v_min)
    out = np.empty(2 * m, dtype=float)
    out[0::2] = first
    out[1::2] = second
    return out, bucket


def to_json_list(values: np.ndarray, digits: int = 3) -> list[float | None]:
    """Round and turn NaN into None (JSON null)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        rounded = np.round(values.astype(float), digits)
    return [None if not math.isfinite(v) else float(v) for v in rounded.tolist()]


def round_or_none(value: float | None, digits: int = 2) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)
