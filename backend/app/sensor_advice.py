"""Likely causes and next steps for an alert, written for the optoNCDT 5500 from its operating instructions.

A report for a sensor with a known data sheet names the physics of this sensor (laser triangulation, diffuse
reflection, extended measuring range, warm-up, temperature coefficient) and quotes the manual page for every
statement taken from it. A line without a page is general engineering knowledge. As everywhere in the cockpit,
causes are "likely" and next steps are checks to carry out, never a diagnosis.
"""

from __future__ import annotations

from typing import Any

from .config import SensorConfig
from .sensor_specs import PAGE, WARMUP_MIN, SensorSpec, main_reason, reasons_text


def cite(*keys: str) -> str:
    pages = sorted({PAGE[key] for key in keys})
    return f"(manual p. {pages[0]})" if len(pages) == 1 else f"(manual pp. {', '.join(str(p) for p in pages)})"


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _vibration(spec: SensorSpec, n: dict[str, Any], related: list[str]) -> tuple[list[str], list[str]]:
    freq, peak = _number(n.get("frequency_hz")), _number(n.get("peak"))
    causes = []
    if related:
        causes.append(f"The same vibration reached {len(related) + 1} sensors at the same moment, so it most "
                      "likely came through the shared fixture, not from one sensor.")
    where = f" at {freq:g} Hz ({freq * 60:.0f} per minute)" if freq else ""
    causes.append(f"Motion between the sensor and the target{where}, for example an impact, a nearby machine or a "
                  f"loose clamp. In the µm range the manual asks for a stable or vibration-damped mounting of "
                  f"sensor and target {cite('vibration')}.")
    if peak is not None and peak > spec.linearity_um:
        causes.append(f"The peak of {peak:.1f} µm is {peak / spec.linearity_um:.0f} times the data sheet linearity "
                      f"of ±{spec.linearity_um:g} µm {cite('linearity')}, so it is much more than the sensor's own "
                      "error: the motion is most likely in the setup.")
    causes.append("Resonance of the sensor holder, which rings at its own frequency after a knock.")
    actions = [
        "Check both mounting points of the sensor (2 × M4 direct or M3 screws, with the centering sleeves) and the "
        f"stiffness of the holder {cite('general')}.",
        f"Look for machines or drives running near {freq:g} Hz." if freq
        else "Look for nearby machines or drives that could excite the fixture.",
        "Check that the target is fixed and does not move with the process.",
        "If it repeats, damp or decouple the holder, or pause measuring while it rings.",
    ]
    return causes, actions


def _noise(spec: SensorSpec) -> tuple[list[str], list[str]]:
    causes = [
        "A dark or shiny target: the sensor evaluates the diffuse part of the reflection and needs a longer "
        f"exposure on such surfaces, and the longest exposure is tied to the measuring rate {cite('reflectance')}.",
        "Translucent or changing material: laser light that penetrates the surface changes the apparent spot size "
        f"and adds measuring uncertainty {cite('reflectance')}.",
        f"A rough or structured surface: roughness of about 5 µm and more shows up as an apparent change of "
        f"distance when the target moves {cite('roughness')}.",
        f"Ambient light on a shiny target, mainly at a reduced measuring rate or near welding equipment "
        f"{cite('ambient')}.",
        "Little averaging in the sensor: the data sheet linearity is given with median 9 + moving average 4096 "
        f"{cite('linearity')}, while the quick setting dynamic is median 9 only {cite('presets')}.",
    ]
    actions = [
        f"Clean the optical windows of the sensor and do not touch them; dirt impairs the function {cite('optics')}.",
        "On the sensor's web page (Home > Signal quality) choose a calmer setting: static is median 9 + moving "
        f"average 128, balanced is 64 {cite('presets')}. A moving average can go up to 4096 {cite('averaging')}.",
        "Pick the preset that fits the surface: Standard for ceramics and metals, Multi-surface for PCBs and "
        f"hybrid materials, Light penetration for plastics such as Teflon or POM {cite('surfaces')}.",
        f"Shield the measurement from ambient light or switch on background suppression {cite('ambient')}.",
        f"Let the sensor warm up for at least {WARMUP_MIN} minutes before judging µm-level noise {cite('warmup')}.",
    ]
    return causes, actions


def _drift(spec: SensorSpec, n: dict[str, Any], forecast_min: float | None) -> tuple[list[str], list[str]]:
    rate = _number(n.get("rate_per_min"))
    per_k = spec.temperature_um_per_k
    temperature = (f"Temperature of the sensor: the data sheet allows up to ±{spec.temperature_pct_fso_per_k:g} % "
                   f"FSO per K, that is {per_k:.2f} µm per K on a metal holder between 20 and 50 °C "
                   f"{cite('temperature')}.")
    if rate is not None and per_k > 0:
        kelvin = abs(rate) / per_k
        if kelvin >= 1.0:
            temperature += (f" The measured drift of {abs(rate):.1f} µm per minute would need the sensor to change "
                            f"by about {kelvin:.1f} K every minute, which is a lot for the sensor body, so the "
                            "holder, the target or the setup is the more likely source.")
        else:
            temperature += (f" A temperature change of about {kelvin:.1f} K per minute would be enough to explain "
                            f"the measured {abs(rate):.1f} µm per minute.")
    causes = [
        temperature,
        f"Warm-up: the manual asks for at least {WARMUP_MIN} minutes after switching on {cite('warmup')}.",
        "Thermal expansion of the holder or the target. Steel grows about 12 µm per metre and degree Celsius.",
        "Slow mechanical creep of the mounting, or wear on the measured surface.",
    ]
    actions = [
        "Check the temperature of the sensor, the holder and the target against the start of the shift. The "
        f"temperature specification holds on a metallic holder with good heat dissipation {cite('temperature')}.",
        f"Make sure the sensor has been switched on for at least {WARMUP_MIN} minutes {cite('warmup')}.",
        "Measure a reference part to tell part drift from setup drift.",
        (f"Check the reference within {forecast_min:.0f} minutes, before the reading leaves the measuring range."
         if forecast_min else "Check the reference, then watch whether the trend returns."),
    ]
    return causes, actions


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
    if kind == "vibration":
        return _vibration(spec, numbers, related)
    if kind == "noise":
        return _noise(spec)
    if kind == "drift":
        return _drift(spec, numbers, event.get("forecast_min"))
    if kind == "out_of_range" and cfg.tolerance_kind == "measuring_range":
        return _out_of_range(spec, numbers, cfg)
    if kind == "invalid":
        return _invalid(spec, numbers)
    return None
