"""Likely causes and next steps for an alert, written for the optoNCDT 5500 from its operating instructions.

A report for a sensor with a known data sheet names the physics of this sensor (laser triangulation, diffuse
reflection, extended measuring range, warm-up, temperature coefficient) and quotes the manual page for every
statement taken from it. A line without a page is general engineering knowledge. As everywhere in the cockpit,
causes are "likely" and next steps are checks to carry out, never a diagnosis.
"""

from __future__ import annotations

from typing import Any

from .config import SensorConfig
from .sensor_specs import DS_PAGE, PAGE, SensorSpec, main_reason, reasons_text


def _pages(label: str, pages: list[int]) -> str:
    return f"{label} p. {pages[0]}" if len(pages) == 1 else f"{label} pp. {', '.join(str(p) for p in pages)}"


def cite(*keys: str) -> str:
    """Where a statement comes from: the data sheet for the technical data table, the manual for the rest."""
    sheet = sorted({DS_PAGE[key] for key in keys if key in DS_PAGE})
    manual = sorted({PAGE[key] for key in keys if key not in DS_PAGE})
    parts = ([_pages("data sheet", sheet)] if sheet else []) + ([_pages("manual", manual)] if manual else [])
    return f"({'; '.join(parts)})"


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _out_of_range(spec: SensorSpec, n: dict[str, Any], cfg: SensorConfig) -> tuple[list[str], list[str]]:
    value = _number(n.get("value"))
    where = f" at {cfg.pos(value)}" if value is not None else ""
    causes = [
        f"The target is outside the measuring range{where}: the sensor reports {spec.range_text} "
        f"({spec.start_mm:g} to {spec.end_mm:g} mm from the sensor). Linearity and repeatability of the data "
        f"sheet apply only inside it {cite('linearity')}.",
        f"In the extended range ({spec.extended_text}) the sensor still delivers values and its state LED flashes "
        f"green {cite('led', 'extended')}, so the reading is usable but not within the guaranteed accuracy.",
        "The target, the part thickness or the position of the sensor changed since the setup, for example a "
        "holder that moved.",
    ]
    actions = [
        f"Move the target or the sensor so that the reading is between 0 and {spec.measuring_range_mm:g} mm, "
        f"ideally near the middle ({spec.measuring_range_mm / 2:g} mm), where the data sheet values are given "
        f"{cite('range')}.",
        f"Look at the state LED of the sensor: green is inside the range, flashing green is the extended range, "
        f"red means no distance value {cite('led')}.",
        "Check the holder of the sensor for displacement.",
    ]
    return causes, actions


def _invalid(spec: SensorSpec, n: dict[str, Any]) -> tuple[list[str], list[str]]:
    reasons = n.get("reasons") if isinstance(n.get("reasons"), dict) else None
    main = main_reason(reasons)
    causes: list[str] = []
    if reasons:
        causes.append(f"The sensor itself says why: {reasons_text(reasons)} (status codes of the Ethernet output "
                      f"{cite('status_codes')}).")
    by_reason = {
        "no_peak": [
            "No target in the laser spot, or the surface returns too little diffuse light. Dark or shiny "
            f"targets need a longer exposure, which is limited by the measuring rate {cite('reflectance')}. "
            f"The state LED is red then {cite('led')}.",
            f"A tilted target: below 5° only strongly reflecting surfaces cause problems {cite('tilt')}.",
        ],
        "before_range": [f"The target is closer than the extended start of the measuring range, that is closer than "
                         f"{spec.ext_start_mm:g} mm from the sensor {cite('extended')}."],
        "after_range": [f"The target is farther than the extended end of the measuring range, that is farther than "
                        f"{spec.ext_end_mm:g} mm from the sensor {cite('extended')}."],
        "cannot_evaluate": [
            "The sensor could not evaluate the measurement value. Reflections that do not come from the target, "
            "for example from between target and optics, can cause false detections on highly reflective targets, "
            f"mainly at low measuring rates {cite('status_codes', 'peak_errors')}."],
        "peak_too_wide": [
            "The peak on the receiver is too wide. Translucent materials let the laser penetrate the surface and "
            f"can widen it; the Light penetration preset is made for them {cite('surfaces')}."],
        "laser_off": [f"The laser is switched off, so the state LED is off {cite('led')}."],
    }
    causes.extend(by_reason.get(main or "no_peak", by_reason["no_peak"]))
    if not reasons:
        causes.extend(["Target outside the measuring range, or tilted too far.",
                       "Something blocking the laser beam."])
    actions_by_reason = {
        "no_peak": [
            "Check that the visible laser spot hits the target and that nothing blocks the beam.",
            "For a dark or shiny surface try the Multi-surface preset, or lower the measuring rate so that the "
            f"sensor can expose longer {cite('reflectance', 'surfaces')}.",
        ],
        "before_range": [f"Move the target away from the sensor until the reading is between 0 and "
                         f"{spec.measuring_range_mm:g} mm."],
        "after_range": [f"Move the target closer to the sensor until the reading is between 0 and "
                        f"{spec.measuring_range_mm:g} mm."],
        "cannot_evaluate": [
            f"At a low measuring rate limit the longest exposure with EXPOSURELIMIT: for this model "
            f"{spec.exposure_limit_us} µm is recommended below {spec.exposure_limit_below_khz:g} kHz. It is only "
            f"available in the Professional user mode {cite('peak_errors', 'exposure_limit')}."],
        "peak_too_wide": ["Try the Light penetration preset, and check the surface of the target "
                          f"{cite('surfaces')}."],
        "laser_off": ["Switch the laser on again, in the sensor's web page or at its laser on/off input."],
    }
    actions = list(actions_by_reason.get(main or "no_peak", actions_by_reason["no_peak"]))
    actions.append(f"Check that the optical windows are clean {cite('optics')}.")
    return causes, actions


def causes_and_actions(event: dict[str, Any], cfg: SensorConfig,
                       related: list[str]) -> tuple[list[str], list[str]] | None:
    """Causes and next steps for this sensor's data sheet, or None for a sensor without one (or a kind
    that the generic text already covers well)."""
    spec = cfg.spec
    if spec is None:
        return None
    kind, numbers = event["kind"], event.get("numbers", {})
    if kind == "out_of_range" and cfg.tolerance_kind == "measuring_range":
        return _out_of_range(spec, numbers, cfg)
    if kind == "invalid":
        return _invalid(spec, numbers)
    return None
