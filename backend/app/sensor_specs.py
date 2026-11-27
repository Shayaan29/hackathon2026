"""What the data sheet says about the sensor.

The cockpit shows the readings of a real optoNCDT 5500 (ILD5500) the way the sensor reports them and takes its
limits from the operating instructions (``man--optoNCDT-5500--en.pdf``). Nothing in this file is measured: it
is the manufacturer's statement. Every value therefore carries the printed page it comes from, so a report or
the copilot can say where a number comes from.

Coordinates. The distance DIST1 that the sensor sends over Ethernet is counted from the start of the measuring
range (SMR): 0 mm at SMR, the measuring range in mm at the end (EMR), and negative values or values above the
range inside the extended measuring range. The manual states it for the RS422 output as
``(value - 98232) / 65536 * measuring range`` in mm (table 8.1, page 62), shows the same mm scale for Ethernet
(figure 3.1, page 11) and allows negative Ethernet values (table 9.1, page 66). This module counts everything
in micrometres from SMR, which is how the cockpit's signal looks when ``reference_mm: range_start`` is set.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

MANUAL = "optoNCDT 5500 operating instructions"

# printed page numbers of the manual
PAGE = {
    "terms": 11, "general": 12, "range": 12, "linearity": 12, "spot": 13, "temperature": 13, "led": 13,
    "reflectance": 15, "ambient": 15, "warmup": 15, "vibration": 16, "motion": 16, "roughness": 16, "tilt": 16,
    "exposure_limit": 17, "extended": 19, "presets": 29, "surfaces": 30, "exposure": 43, "averaging": 47,
    "mastering": 48, "rs422": 62, "ethernet": 66, "status_codes": 67, "optics": 14, "peak_errors": 16, "fso": 75,
}

# general data of the whole family (chapter 3.3, page 12; warm-up page 15)
RATE_MIN_KHZ, RATE_MAX_KHZ, RATE_FACTORY_KHZ = 0.25, 150.0, 4.0
AMBIENT_LUX, AMBIENT_LUX_DEFAULT_20KHZ = 200_000, 50_000
WARMUP_MIN = 30
VIBRATION_RATING = "50 g, 20 to 500 Hz"
SHOCK_RATING = "15 g, 6 ms"
OPERATING_C = (0, 50)


def _g(value: float) -> str:
    return f"{value:g}"


def _mm(um: float, digits: int = 3) -> str:
    """Micrometres from SMR as the sensor's own millimetres: 4.729 mm, 10 mm, -2.5 mm."""
    mm = um / 1000.0
    text = f"{mm:.{digits}f}".rstrip("0").rstrip(".")
    return f"{text if text not in ('-0', '') else '0'} mm"


@dataclass(frozen=True)
class Zone:
    """Where a reading lies in the measuring range, in the words of the sensor's state LED (table 3.1, p. 13)."""

    name: str        # standard | before | after | outside
    led: str         # green | flashing green | red
    text: str


@dataclass(frozen=True)
class SensorSpec:
    family: str
    model: str                       # "ILD5500-10"
    measuring_range_mm: float
    start_mm: float                  # distance from the sensor to the start of the measuring range (SMR)
    mid_mm: float
    end_mm: float
    ext_start_mm: float              # extended start of the measuring range (e-SMR)
    ext_end_mm: float                # extended end of the measuring range (e-EMR)
    linearity_um: float
    linearity_pct_fso: float
    repeatability_um: float
    spot_smr_um: tuple[int, int]
    spot_mmr_um: tuple[int, int]
    spot_emr_um: tuple[int, int]
    smallest_spot_um: tuple[int, int] | None
    smallest_spot_at_mm: float | None
    temperature_pct_fso_per_k: float
    exposure_limit_below_khz: float  # EXPOSURELIMIT is recommended below this measuring rate (p. 17)
    exposure_limit_us: int

    # -- coordinates, micrometres from the start of the measuring range ---------------------------------------

    @property
    def low_um(self) -> float:
        return 0.0

    @property
    def mid_um(self) -> float:
        return self.measuring_range_mm * 500.0

    @property
    def high_um(self) -> float:
        return self.measuring_range_mm * 1000.0

    @property
    def ext_low_um(self) -> float:
        return (self.ext_start_mm - self.start_mm) * 1000.0

    @property
    def ext_high_um(self) -> float:
        return (self.ext_end_mm - self.start_mm) * 1000.0

    @property
    def temperature_um_per_k(self) -> float:
        """Largest shift per kelvin: percent of the full scale (FSO is the measuring range, page 75)."""
        return self.temperature_pct_fso_per_k / 100.0 * self.high_um

    @property
    def title(self) -> str:
        return f"optoNCDT {self.model}"

    @property
    def range_text(self) -> str:
        return f"0 to {_g(self.measuring_range_mm)} mm"

    @property
    def extended_text(self) -> str:
        return f"{_g(self.ext_low_um / 1000)} to {_g(self.ext_high_um / 1000)} mm"

    def zone(self, position_um: float) -> Zone:
        if self.low_um <= position_um <= self.high_um:
            return Zone("standard", "green", f"inside the measuring range ({self.range_text})")
        if self.ext_low_um <= position_um < self.low_um:
            return Zone("before", "flashing green", "closer than the measuring range, in the extended range")
        if self.high_um < position_um <= self.ext_high_um:
            return Zone("after", "flashing green", "beyond the measuring range, in the extended range")
        return Zone("outside", "red", "outside even the extended measuring range")

    def public(self) -> dict[str, Any]:
        """What the browser needs to draw the range gauge and to speak in the sensor's terms."""
        return {
            "family": self.family, "model": self.model, "title": self.title,
            "measuring_range_mm": self.measuring_range_mm,
            "start_mm": self.start_mm, "mid_mm": self.mid_mm, "end_mm": self.end_mm,
            "zones_um": {"ext_low": self.ext_low_um, "low": self.low_um, "mid": self.mid_um,
                         "high": self.high_um, "ext_high": self.ext_high_um},
            "linearity_um": self.linearity_um, "repeatability_um": self.repeatability_um,
            "temperature_um_per_k": round(self.temperature_um_per_k, 3),
            "manual": MANUAL,
        }

    # -- text for reports and the copilot -------------------------------------------------------------------

    def datasheet_rows(self, live_rate_khz: float | None = None) -> list[dict[str, str | int]]:
        """The data sheet as rows (label, value, page) for the report table."""
        rate = f"{_g(RATE_MIN_KHZ)} to {_g(RATE_MAX_KHZ)} kHz, factory setting {_g(RATE_FACTORY_KHZ)} kHz"
        if live_rate_khz:
            rate += f"; this sensor streams {_g(round(live_rate_khz, 2))} kHz"
        mid, a, b = self.spot_mmr_um, self.spot_smr_um, self.spot_emr_um
        return [
            {"label": "Model", "value": f"{self.title}, measuring range {_g(self.measuring_range_mm)} mm",
             "page": PAGE["range"]},
            {"label": "Measuring range",
             "value": (f"{_g(self.start_mm)} to {_g(self.end_mm)} mm from the sensor; "
                       f"the sensor reports it as {self.range_text}, mid {_g(self.measuring_range_mm / 2)} mm"),
             "page": PAGE["range"]},
            {"label": "Extended measuring range",
             "value": (f"{_g(self.ext_start_mm)} to {_g(self.ext_end_mm)} mm from the sensor, "
                       f"readings {self.extended_text}; the state LED flashes green there"),
             "page": PAGE["extended"]},
            {"label": "Linearity",
             "value": (f"< ±{_g(self.linearity_um)} µm (±{_g(self.linearity_pct_fso)} % FSO), standard range "
                       "only; with median 9 + moving average 4096, 20 kHz, mid of range, white ceramic"),
             "page": PAGE["linearity"]},
            {"label": "Repeatability",
             "value": f"< {_g(self.repeatability_um)} µm, with median 9 + moving average 4096 at 150 kHz",
             "page": PAGE["linearity"]},
            {"label": "Light spot",
             "value": (f"{mid[0]} × {mid[1]} µm at mid range ({a[0]} × {a[1]} µm at the start, "
                       f"{b[0]} × {b[1]} µm at the end)"),
             "page": PAGE["spot"]},
            {"label": "Temperature stability",
             "value": (f"±{_g(self.temperature_pct_fso_per_k)} % FSO per K, that is up to "
                       f"{self.temperature_um_per_k:.2f} µm per K on a metal holder, 20 to 50 °C"),
             "page": PAGE["temperature"]},
            {"label": "Measuring rate", "value": rate, "page": PAGE["general"]},
            {"label": "Ambient light",
             "value": (f"up to {AMBIENT_LUX:,} lx with background suppression; "
                       f"{AMBIENT_LUX_DEFAULT_20KHZ:,} lx with the default settings at 20 kHz"),
             "page": PAGE["general"]},
            {"label": "Robustness",
             "value": (f"IP67, vibration {VIBRATION_RATING}, shock {SHOCK_RATING}, "
                       f"operating {OPERATING_C[0]} to {OPERATING_C[1]} °C"),
             "page": PAGE["general"]},
            {"label": "Warm-up", "value": f"at least {WARMUP_MIN} minutes before measuring in the µm range",
             "page": PAGE["warmup"]},
        ]

    def summary(self) -> str:
        """Two sentences for the copilot's instructions."""
        return (f"{self.title}: laser triangulation, measuring range {_g(self.measuring_range_mm)} mm "
                f"({self.start_mm:g} to {self.end_mm:g} mm from the sensor), extended range "
                f"{self.extended_text}, linearity < ±{_g(self.linearity_um)} µm, repeatability "
                f"< {_g(self.repeatability_um)} µm, temperature stability up to "
                f"{self.temperature_um_per_k:.2f} µm per K.")


def _spec(model: str, mr: float, start: float, mid: float, end: float, e_start: float, e_end: float,
          lin: float, lin_pct: float, rep: float, smr: tuple[int, int], mmr: tuple[int, int],
          emr: tuple[int, int], smallest: tuple[int, int] | None, smallest_at: float | None,
          temp: float, exp_khz: float, exp_us: int) -> SensorSpec:
    return SensorSpec("optoNCDT 5500", model, mr, start, mid, end, e_start, e_end, lin, lin_pct, rep,
                      smr, mmr, emr, smallest, smallest_at, temp, exp_khz, exp_us)


# chapter 3.4 (pages 12, 13), table 5.4 / 5.5 (page 19) and the EXPOSURELIMIT table (page 17)
CATALOGUE: dict[int, SensorSpec] = {
    10: _spec("ILD5500-10", 10, 30, 35, 40, 27.5, 42.5, 1.5, 0.015, 0.008,
              (85, 200), (60, 75), (130, 250), (30, 47), 34.5, 0.008, 3.33, 300),
    25: _spec("ILD5500-25", 25, 40, 52.5, 65, 35, 72.5, 5.0, 0.015, 0.013,
              (140, 310), (60, 90), (230, 380), (46, 66), 51.1, 0.008, 1.0, 1000),
    50: _spec("ILD5500-50", 50, 45, 70, 95, 40, 115, 7.5, 0.015, 0.021,
              (270, 530), (130, 150), (325, 570), (58, 82), 68.1, 0.008, 1.6, 600),
    100: _spec("ILD5500-100", 100, 70, 120, 170, 55, 205, 30.0, 0.02, 0.042,
               (200, 500), (200, 500), (640, 1100), (82, 117), 99.0, 0.02, 0.25, 4000),
    200: _spec("ILD5500-200", 200, 100, 200, 300, 70, 370, 80.0, 0.03, 0.098,
               (780, 1800), (780, 1800), (780, 1800), None, None, 0.02, 0.8, 1300),
}

_MODEL = re.compile(r"ild\s*-?\s*5500\s*[-_ ]?\s*(10|25|50|100|200)(?!\d)", re.IGNORECASE)


def find_spec(text: str | None) -> SensorSpec | None:
    """The data sheet for a model text such as 'optoNCDT ILD5500-10', or None when the model is unknown."""
    if not text:
        return None
    match = _MODEL.search(str(text))
    return CATALOGUE.get(int(match.group(1))) if match else None


def known_models() -> str:
    return ", ".join(spec.model for spec in CATALOGUE.values())


# -- why a reading is invalid (status codes of the Ethernet output, page 67) ----------------------------------------------

REASON_TEXT = {
    "no_peak": "no peak found (no target in view, or too little light coming back)",
    "before_range": "target closer than the extended measuring range",
    "after_range": "target beyond the extended measuring range",
    "cannot_evaluate": "reading cannot be evaluated",
    "peak_too_wide": "peak too wide (surface or light problem)",
    "laser_off": "laser is off",
    "other": "a status code the cockpit does not know",
}


def main_reason(reasons: dict[str, float] | None) -> str | None:
    """The reason with the largest share, or None when nothing is known."""
    if not reasons:
        return None
    return max(reasons.items(), key=lambda item: item[1])[0]


def reasons_text(reasons: dict[str, float] | None) -> str:
    """'mostly no peak found (...), 18 % target beyond ...' for an alert summary."""
    if not reasons:
        return ""
    ranked = sorted(reasons.items(), key=lambda item: -item[1])
    first, share = ranked[0]
    text = REASON_TEXT.get(first, first)
    out = f"mostly {text}" if share >= 0.6 else f"{round(share * 100)} % {text}"
    if len(ranked) > 1 and share < 0.95:
        second, share2 = ranked[1]
        out += f", {round(share2 * 100)} % {REASON_TEXT.get(second, second)}"
    return out


def invalid_hint(reason: str | None, spec: SensorSpec | None = None) -> str:
    """One plain sentence on what to check, for the most common reason of the invalid readings."""
    extended = spec.extended_text if spec else "the extended measuring range"
    hints = {
        "no_peak": "No target in the laser spot, or too little light comes back from it (dark, shiny or tilted surface).",
        "before_range": f"The target is closer than the extended measuring range ({extended}); move it back.",
        "after_range": f"The target is farther than the extended measuring range ({extended}); move it closer.",
        "cannot_evaluate": "The sensor cannot evaluate the reading; look for stray reflections or an unsuitable surface.",
        "peak_too_wide": ("The light spot looks too wide for the sensor; check the surface (translucent or very rough "
                          "material) and the preset."),
        "laser_off": "The laser is switched off; switch it on in the sensor's web page or at its laser input.",
    }
    return hints.get(reason or "", "The target may be out of range, tilted, or too shiny or dark.")
