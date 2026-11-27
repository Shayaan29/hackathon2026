"""Incident reports: everything a maintenance technician needs about one alert.

``build`` collects the facts (event, signal around it, analysis, timeline,
likely causes, actions, settings check). ``render_html`` turns them into one
standalone page with inline SVG charts, so the same report works on the
cockpit, on a phone and as a printed PDF.
"""

from __future__ import annotations

import html
import math
import time
from datetime import datetime
from typing import Any

import numpy as np

from . import assistant, sensor_advice, views
from .analysis import decimate_minmax, round_or_none
from .engine import Engine
from .sensor_specs import MANUAL, reasons_text
from .tools import analyze


class ReportError(LookupError):
    pass


KIND_TITLE = {
    "vibration": "Vibration", "noise": "Increased noise", "drift": "Drift",
    "out_of_range": "Out of tolerance", "invalid": "Invalid readings",
}


def kind_title(kind: str, limit_word: str = "tolerance") -> str:
    """The alert title; a sensor whose limits are its measuring range says so."""
    if kind == "out_of_range" and limit_word == "measuring range":
        return "Out of measuring range"
    return KIND_TITLE.get(kind, kind)
PRIORITY = {"critical": "P1 (urgent)", "warning": "P2 (today)", "info": "P3 (planned)"}


def _causes_and_actions(event: dict[str, Any], unit: str, related: list[str]) -> tuple[list[str], list[str]]:
    n = event.get("numbers", {})
    kind = event["kind"]
    if kind == "vibration":
        f = n.get("frequency_hz")
        causes = [
            f"Mechanical excitation of the fixture or machine table at {f} Hz "
            f"({float(f) * 60:.0f} per minute), for example an impact, a nearby machine or a loose clamp."
            if f else "Mechanical excitation of the fixture or machine table.",
            "Resonance of the sensor bracket, which rings at its own frequency after a knock.",
        ]
        if related:
            causes.insert(0, f"The same vibration reached {len(related) + 1} sensors at the same moment, so "
                             "it most likely came through the shared fixture, not from one sensor.")
        actions = ["Check the mounting screws and the stiffness of the sensor bracket.",
                   f"Look for machines or drives running near {f} Hz." if f else
                   "Look for nearby machines or drives that could excite the fixture.",
                   "If it repeats, decouple or damp the fixture, or pause measuring while it rings."]
    elif kind == "drift":
        causes = ["Thermal expansion of the fixture or the part. Steel grows about 12 µm per metre "
                  "and degree Celsius.",
                  "Sensor or machine still warming up.",
                  "Slow mechanical creep of the mounting, or wear on the measured surface."]
        fm = event.get("forecast_min")
        actions = ["Check ambient and machine temperature against the start of the shift.",
                   "Measure a reference part to tell part drift from setup drift.",
                   f"Re-zero or recalibrate within {fm:.0f} minutes, before the tolerance limit is reached."
                   if fm else "Re-zero or recalibrate the setup, then watch whether the trend returns."]
    elif kind == "noise":
        causes = ["Dirty sensor window or optics.",
                  "Difficult target surface: shiny, dark or structured.",
                  "Ambient light or electrical interference on the sensor cable.",
                  "Averaging set too low for this surface."]
        actions = ["Clean the sensor window.",
                   "Check cable shielding and grounding.",
                   "Apply the settings the setup assistant suggests below."]
    elif kind == "out_of_range":
        causes = ["The part itself is outside tolerance.",
                  "The sensor or fixture moved, so the reference no longer matches."]
        actions = ["Check the part with a reference gauge.",
                   "Hold back parts measured since this alert until the cause is clear.",
                   "Check the sensor mounting for displacement."]
    else:  # invalid
        causes = ["Target outside the measuring range, or tilted too far.",
                  "Surface too shiny or too dark for the current exposure.",
                  "Something blocking the laser beam."]
        actions = ["Check distance and tilt of the target.",
                   "Check the beam path for obstructions or coolant.",
                   "Switch exposure to intelligent if the surface changes."]
    return causes, actions


def find_event(engine: Engine, event_id: str | None) -> dict[str, Any]:
    events = engine.recent_events(limit=500)
    if not events:
        raise ReportError("There are no alerts yet.")
    if not event_id or event_id == "latest":
        return events[-1]
    for event in events:
        if event["id"] == event_id:
            return event
    raise ReportError(f"Unknown alert {event_id!r}.")


def _signal_around(engine: Engine, sid: str, t: float) -> dict[str, Any]:
    buf = engine.buffers[sid]
    t0 = t - 3.0
    t1 = min(t + 4.0, buf.t_last or t + 4.0)
    start, dt, values = buf.fast_range(t0, t1)
    if values.size == 0:
        return {"t": [], "v": []}
    dec, _ = decimate_minmax(values.astype(float), 600)
    end = start + (values.size - 1) * dt
    rel = np.linspace(start - t, end - t, dec.size)
    return {"t": [round(float(x), 3) for x in rel], "v": [round_or_none(float(v), 3) for v in dec]}


def build(engine: Engine, event_id: str | None = None) -> dict[str, Any]:
    event = find_event(engine, event_id)
    sid = event["sensor"]
    cfg = engine.settings.sensor(sid)
    related = [s for s in event.get("related_sensors", []) if s in engine.buffers]
    involved = [sid] + related
    sheet = sensor_advice.causes_and_actions(event, cfg, related)
    causes, actions = sheet if sheet is not None else _causes_and_actions(event, cfg.unit, related)
    display = cfg.display.as_dict() if cfg.display else None

    charts: list[dict[str, Any]] = [{
        "type": "signal", "title": f"{engine.settings.sensor(s).name} around the alert",
        "sensor": s, **_signal_around(engine, s, event["t"]),
        "tolerance": {"low": engine.settings.sensor(s).tolerance.low,
                      "high": engine.settings.sensor(s).tolerance.high},
        "display": engine.settings.sensor(s).display.as_dict() if engine.settings.sensor(s).display else None,
    } for s in involved]
    if event["kind"] == "vibration":
        spec = views.spectrum(engine, sid, event_id=event["id"])
        charts.append({"type": "spectrum", "title": f"Frequency content, {cfg.name}",
                       "freqs": spec["freqs"], "amps": spec["amps"], "peak": spec.get("peak")})
    if event["kind"] == "drift":
        d = views.drift(engine, sid)
        charts.append({"type": "drift", "title": f"Trend and forecast, {cfg.name}", "display": display,
            "limit_word": cfg.limit_word, **{
            k: d.get(k) for k in ("t_rel", "values", "fit", "forecast", "tolerance")}})

    try:
        analysis = analyze(engine, sid, 120.0)
        observations = analysis.get("observations", [])
    except Exception:  # analysis is a bonus, the report must still render
        observations = []
    setup = assistant.suggest(engine, sid)
    timeline = [e for e in engine.recent_events(limit=50, since=event["t"] - 900)
                if e["sensor"] in involved or set(e.get("related_sensors", [])) & set(involved)]
    title = f"{kind_title(event['kind'], cfg.limit_word)} on {cfg.name}"

    live = engine._live_status(sid)
    datasheet = None
    if cfg.spec is not None:
        rate = (live or {}).get("rate_hz")
        datasheet = {"title": cfg.spec.title, "manual": MANUAL,
                     "rows": cfg.spec.datasheet_rows(rate / 1000.0 if rate else None)}
    ticket_sensor = ([f"Sensor: {cfg.spec.title}, measuring range {cfg.spec.range_text}, linearity "
                      f"< ±{cfg.spec.linearity_um:g} µm ({MANUAL}, p. 12)"] if cfg.spec is not None else [])
    ticket = "\n".join([
        f"Title: {title}",
        f"Priority: {PRIORITY.get(event['severity'], event['severity'])}",
        f"Component: {cfg.name} ({cfg.model}), fixture {cfg.fixture}",
        *ticket_sensor,
        f"Detected: {datetime.fromtimestamp(event['t']).strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        f"What happened: {event['summary']}",
        "",
        "Likely causes:", *[f"- {c}" for c in causes],
        "",
        "Next steps:", *[f"- {a}" for a in actions],
        "",
        f"Setup check: {setup['summary']}",
    ])
    return {
        "id": event["id"], "title": title, "generated_at": time.time(),
        "cockpit": {"title": engine.settings.cockpit.title, "team": engine.settings.cockpit.team},
        "event": event, "priority": PRIORITY.get(event["severity"], event["severity"]),
        "sensors": [{"id": s, "name": engine.settings.sensor(s).name,
                     "model": engine.settings.sensor(s).model, "unit": engine.settings.sensor(s).unit,
                     "fixture": engine.settings.sensor(s).fixture,
                     "display": (engine.settings.sensor(s).display.as_dict()
                                 if engine.settings.sensor(s).display else None),
                     "limit_word": engine.settings.sensor(s).limit_word,
                     "tolerance": [engine.settings.sensor(s).tolerance.low,
                                   engine.settings.sensor(s).tolerance.high]} for s in involved],
        "datasheet": datasheet,
        "source": None if not live else {
            "kind": live.get("kind"), "host": live.get("host"), "port": live.get("port"),
            "rate_hz": live.get("rate_hz"), "serial": live.get("serial")},
        "observations": observations, "causes": causes, "actions": actions,
        "setup": {"status": setup["status"], "summary": setup["summary"], "proposal": setup["proposal"],
                  "reasons": setup["reasons"] if setup["status"] == "advice" else []},
        "timeline": [{"t": e["t"], "kind": e["kind"], "severity": e["severity"], "summary": e["summary"]}
                     for e in timeline],
        "charts": charts, "ticket": ticket,
    }


# -- HTML --------------------------------------------------------------------

def _esc(text: Any) -> str:
    return html.escape(str(text))


def _svg_line(xs: list[float], ys: list[float | None], width: int = 820, height: int = 220,
              band: tuple[float, float] | None = None, marker_x: float | None = None,
              extra: list[tuple[list[float], list[float | None], str]] | None = None,
              x_label: str = "", y_unit: str = "", hline: float | None = None, fine: bool = False) -> str:
    pad_l, pad_r, pad_t, pad_b = 52 if not fine else 62, 14, 12, 30
    series = [(xs, ys)] + [(ex, ey) for ex, ey, _ in (extra or [])]
    all_x = [x for sx, _ in series for x in sx]
    all_y = [y for _, sy in series for y in sy if y is not None and math.isfinite(y)]
    if not all_x or not all_y:
        return '<p class="muted">Not enough data for this chart.</p>'
    x0, x1 = min(all_x), max(all_x)
    y0, y1 = min(all_y), max(all_y)
    if band and (min(abs(band[0]), abs(band[1])) <= max(abs(y0), abs(y1)) * 2):
        y0, y1 = min(y0, band[0]), max(y1, band[1])
    if hline is not None:
        y0, y1 = min(y0, hline), max(y1, hline)
    span = (y1 - y0) or 1.0
    y0, y1 = y0 - span * 0.08, y1 + span * 0.08
    if x1 == x0:
        x1 = x0 + 1
    # axis labels in millimetres need as many decimals as the spread of the readings asks for
    decimals = max(1, min(6, math.ceil(-math.log10(max((y1 - y0) / 4.0, 1e-9))) + 1)) if fine else 1

    def px(x: float) -> float:
        return pad_l + (x - x0) / (x1 - x0) * (width - pad_l - pad_r)

    def py(y: float) -> float:
        return pad_t + (y1 - y) / (y1 - y0) * (height - pad_t - pad_b)

    def path(sx: list[float], sy: list[float | None]) -> str:
        out, pen = [], False
        for x, y in zip(sx, sy):
            if y is None or not math.isfinite(y):
                pen = False
                continue
            out.append(f"{'L' if pen else 'M'}{px(x):.1f},{py(y):.1f}")
            pen = True
        return " ".join(out)

    parts = [f'<svg viewBox="0 0 {width} {height}" class="chart" role="img">']
    for i in range(5):
        yv = y0 + (y1 - y0) * i / 4
        parts.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{py(yv):.1f}" y2="{py(yv):.1f}" class="grid"/>'
                     f'<text x="{pad_l - 6}" y="{py(yv) + 4:.1f}" class="ax" text-anchor="end">{yv:.{decimals}f}</text>')
    if band:
        top, bottom = py(min(band[1], y1)), py(max(band[0], y0))
        parts.append(f'<rect x="{pad_l}" y="{top:.1f}" width="{width - pad_l - pad_r}" '
                     f'height="{max(bottom - top, 0):.1f}" class="band"/>')
    if hline is not None:
        parts.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{py(hline):.1f}" y2="{py(hline):.1f}" class="limit"/>')
    if marker_x is not None and x0 <= marker_x <= x1:
        parts.append(f'<line x1="{px(marker_x):.1f}" x2="{px(marker_x):.1f}" y1="{pad_t}" '
                     f'y2="{height - pad_b}" class="marker"/>'
                     f'<text x="{px(marker_x) + 5:.1f}" y="{pad_t + 12}" class="mk">alert</text>')
    parts.append(f'<path d="{path(xs, ys)}" class="line"/>')
    for ex, ey, cls in extra or []:
        parts.append(f'<path d="{path(ex, ey)}" class="{cls}"/>')
    for i in range(5):
        xv = x0 + (x1 - x0) * i / 4
        parts.append(f'<text x="{px(xv):.1f}" y="{height - 10}" class="ax" text-anchor="middle">{xv:.0f}</text>')
    parts.append(f'<text x="{width - pad_r}" y="{height - 10}" class="ax" text-anchor="end">{_esc(x_label)}</text>')
    parts.append(f'<text x="4" y="{pad_t + 4}" class="ax">{_esc(y_unit)}</text></svg>')
    return "".join(parts)


def _scaled(values: list[float | None], scale: float) -> list[float | None]:
    return [None if v is None else v * scale for v in values]


def _chart_html(chart: dict[str, Any], unit: str) -> str:
    display = chart.get("display") if chart["type"] in ("signal", "drift") else None
    scale = float(display["scale"]) if display else 1.0
    shown = display["unit"] if display else unit
    fine = display is not None
    if chart["type"] == "signal":
        tol = chart.get("tolerance") or {}
        band = (tol["low"] * scale, tol["high"] * scale) if tol else None
        svg = _svg_line(chart["t"], _scaled(chart["v"], scale), band=band, marker_x=0.0,
                        x_label="seconds from alert", y_unit=shown, fine=fine)
    elif chart["type"] == "spectrum":
        peak = chart.get("peak") or {}
        svg = _svg_line(chart["freqs"], chart["amps"], x_label="Hz", y_unit=unit)
        if peak:
            svg += f'<p class="cap">Strongest frequency: <b>{peak.get("frequency_hz")} Hz</b></p>'
    else:
        extra, limit, caption = [], None, ""
        forecast = chart.get("forecast")
        word = chart.get("limit_word", "tolerance")
        if isinstance(forecast, dict) and forecast.get("points"):
            pts = forecast["points"]
            extra.append(([p[0] for p in pts], _scaled([p[1] for p in pts], scale), "forecast"))
            crossing = forecast.get("crossing")
            if crossing:
                limit = crossing["value"] * scale
            if forecast.get("minutes_to_limit") is not None:
                caption = (f'<p class="cap">Forecast: {word} limit in about '
                           f'<b>{forecast["minutes_to_limit"]:.0f} minutes</b> (dashed line).</p>')
        tol = chart.get("tolerance") or {}
        band = (tol["low"] * scale, tol["high"] * scale) if tol else None
        svg = _svg_line(chart.get("t_rel") or [], _scaled(chart.get("values") or [], scale), extra=extra,
                        band=band, hline=limit, x_label="seconds (0 = now)", y_unit=shown, fine=fine) + caption
    return f'<figure><figcaption>{_esc(chart["title"])}</figcaption>{svg}</figure>'


STYLE = """
:root{--ink:#15202b;--muted:#66737f;--line:#dde2e7;--accent:#5a3ff2;--warn:#c27400;--crit:#d7263d}
*{box-sizing:border-box}body{margin:0;font-family:"IBM Plex Sans","Segoe UI",system-ui,sans-serif;color:var(--ink);background:#e9ecef}
body.embed{background:#fff}body.embed .doc{margin:0;max-width:none;border-radius:0;box-shadow:none}body.embed .actions{display:none}
.doc{max-width:920px;margin:24px auto;background:#fff;border-radius:14px;box-shadow:0 10px 40px rgba(13,27,42,.12);overflow:hidden}
header{padding:26px 36px;background:var(--ink);color:#eef0f2;border-bottom:3px solid var(--accent)}
header .brand{font-size:14px;opacity:.75}
header h1{margin:8px 0 6px;font-size:28px}header .sub{opacity:.8;font-size:14px}
.chip{display:inline-block;padding:3px 10px;border-radius:999px;font-size:13px;font-weight:600;margin-right:8px;text-transform:capitalize}
.critical{background:var(--crit);color:#fff}.warning{background:var(--warn);color:#fff}.info{background:var(--accent);color:#fff}
main{padding:8px 36px 32px}section{padding:18px 0;border-bottom:1px solid var(--line)}section:last-child{border:0}
h2{font-size:16px;font-weight:600;color:var(--muted);margin:0 0 8px}
.summary{font-size:19px;line-height:1.5;margin:0}
.meta{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.meta div{background:#f5f8fa;border-radius:10px;padding:10px 12px}
.meta b{display:block;font-size:13px;color:var(--muted);margin-bottom:2px;font-weight:500}
ul{margin:0;padding-left:20px;line-height:1.6}figure{margin:0 0 16px}figcaption{font-weight:600;margin-bottom:6px}
.chart{width:100%;height:auto;background:#fbfcfd;border:1px solid var(--line);border-radius:10px}
.chart .grid{stroke:#e8eef3}.chart .ax{fill:#7b8a99;font-size:11px}.chart .line{fill:none;stroke:var(--accent);stroke-width:1.5}
.chart .band{fill:rgba(46,160,67,.08)}.chart .marker{stroke:var(--warn);stroke-width:2;stroke-dasharray:4 3}.chart .mk{fill:var(--warn);font-size:11px;font-weight:700}
.chart .fit{fill:none;stroke:#0d1b2a;stroke-width:2}.chart .forecast{fill:none;stroke:var(--crit);stroke-width:2;stroke-dasharray:6 4}.chart .limit{stroke:var(--crit)}
.cap{margin:6px 0 0;color:var(--muted);font-size:14px}.muted{color:var(--muted)}
table{width:100%;border-collapse:collapse;font-size:14px}td{padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}td:first-child{white-space:nowrap;color:var(--muted);width:90px}
table.sheet td:first-child{width:190px;white-space:normal}table.sheet td.pg{white-space:nowrap;color:var(--muted);width:48px;text-align:right}
.actions{position:sticky;top:0;display:flex;gap:10px;justify-content:flex-end;padding:12px 36px;background:#fff;border-bottom:1px solid var(--line);z-index:2}
.actions button{font:inherit;padding:9px 16px;border-radius:10px;border:1px solid var(--line);background:#fff;cursor:pointer;font-weight:600}
.actions button.primary{background:var(--ink);color:#fff;border-color:var(--ink)}
pre{white-space:pre-wrap;background:#f5f8fa;border-radius:10px;padding:14px;font-size:13px;line-height:1.5}
@media(max-width:640px){header,main,.actions{padding-left:18px;padding-right:18px}.meta{grid-template-columns:1fr 1fr}header h1{font-size:22px}}
@media print{body{background:#fff}.doc{box-shadow:none;margin:0;max-width:none}.actions{display:none}section{break-inside:avoid}}
"""


FACT_LABELS = {
    "frequency_hz": ("Frequency", "{v} Hz"),
    "peak": ("Peak deviation", "{v} {unit}"),
    "times_normal_noise": ("Compared with normal noise", "{v} times higher"),
    "rate_per_min": ("Drift rate", "{v} {unit} per minute"),
    "noise": ("Noise", "{v} {unit}"),
    "invalid_share": ("Invalid readings", "{v}"),
    "invalid_percent": ("Invalid readings", "{v} % of the last second"),
    "r2": ("Straightness of the trend (R²)", "{v}"),
    "range": ("Measuring range of the sensor", "{v}"),
    "extended_range": ("Extended measuring range", "{v}"),
    "led": ("State LED of the sensor", "{v}"),
}
# values that are positions: shown in the sensor's own unit (millimetres for the optoNCDT)
POSITION_FACTS = {"value": "Reading", "limit": "Limit crossed", "current": "Reading now",
                  "tolerance_low": "Lower limit", "tolerance_high": "Upper limit"}
ZONE_TEXT = {"standard": "inside the measuring range", "before": "before the start of the range (extended range)",
             "after": "beyond the end of the range (extended range)", "outside": "outside the extended range"}


def _fact(key: str, value: Any, unit: str, display: dict[str, Any] | None = None) -> str:
    if key in POSITION_FACTS and isinstance(value, (int, float)):
        text = (f"{value * display['scale']:.{display['decimals']}f} {display['unit']}" if display
                else f"{value} {unit}")
        return f"<li>{_esc(POSITION_FACTS[key])}: <b>{_esc(text)}</b></li>"
    if key == "zone":
        return f"<li>Where the target is: <b>{_esc(ZONE_TEXT.get(str(value), value))}</b></li>"
    if key == "reasons" and isinstance(value, dict):
        return f"<li>Why the readings were invalid: <b>{_esc(reasons_text(value))}</b></li>"
    if key == "main_reason":
        return ""
    label, fmt = FACT_LABELS.get(key, (key.replace("_", " ").capitalize(), "{v}"))
    return f"<li>{_esc(label)}: <b>{_esc(fmt.format(v=value, unit=unit))}</b></li>"


def _datasheet_html(sheet: dict[str, Any] | None) -> str:
    if not sheet:
        return ""
    rows = "".join(f"<tr><td>{_esc(r['label'])}</td><td>{_esc(r['value'])}</td>"
                   f"<td class='pg'>p. {r['page']}</td></tr>" for r in sheet["rows"])
    return (f"<section><h2>Sensor data sheet: {_esc(sheet['title'])}</h2><table class='sheet'>{rows}</table>"
            f"<p class='cap'>From the {_esc(sheet['manual'])}. Page numbers are the printed ones.</p></section>")


def render_html(report: dict[str, Any], embed: bool = False) -> str:
    event = report["event"]
    unit = report["sensors"][0]["unit"] if report["sensors"] else ""
    when = datetime.fromtimestamp(event["t"]).strftime("%d %b %Y, %H:%M:%S")
    sev = event["severity"]
    sensors_txt = ", ".join(s["name"] for s in report["sensors"])
    source = report.get("source")
    source_txt = ""
    if source:
        rate = f", {source['rate_hz'] / 1000:g} kHz" if source.get("rate_hz") else ""
        source_txt = f"Live sensor at {source.get('host')}{rate}"
    meta_items = [("Detected", when), ("Priority", report["priority"]), ("Sensors", sensors_txt),
                  ("Sensor model", report["sensors"][0]["model"] if report["sensors"] else "")]
    if source_txt:
        meta_items.append(("Data source", source_txt))
    meta = "".join(f"<div><b>{_esc(k)}</b>{_esc(v)}</div>" for k, v in meta_items)
    numbers = event.get("numbers", {})
    display = report["sensors"][0].get("display") if report["sensors"] else None
    facts = "".join(_fact(k, v, numbers.get("unit", unit), display) for k, v in numbers.items() if k != "unit")
    obs = "".join(f"<li>{_esc(o)}</li>" for o in report["observations"])
    causes = "".join(f"<li>{_esc(c)}</li>" for c in report["causes"])
    actions = "".join(f"<li>{_esc(a)}</li>" for a in report["actions"])
    charts = "".join(_chart_html(c, unit) for c in report["charts"])
    timeline = "".join(
        f"<tr><td>{datetime.fromtimestamp(e['t']).strftime('%H:%M:%S')}</td>"
        f"<td><span class='chip {e['severity']}'>{_esc(e['kind'].replace('_', ' '))}</span>{_esc(e['summary'])}</td></tr>"
        for e in report["timeline"])
    setup = report["setup"]
    setup_html = f"<p>{_esc(setup['summary'])}</p>"
    if setup.get("reasons"):
        setup_html += "<ul>" + "".join(f"<li>{_esc(r)}</li>" for r in setup["reasons"]) + "</ul>"
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(report['title'])}: incident report</title><style>{STYLE}</style></head>
<body class="{'embed' if embed else ''}"><div class="doc">
<div class="actions"><button onclick="copyTicket(this)">Copy ticket text</button>
<button class="primary" onclick="window.print()">Save as PDF</button></div>
<header><div class="brand">Incident report {_esc(report['id'])} from {_esc(report['cockpit']['title'])}</div>
<h1>{_esc(report['title'])}</h1>
<div class="sub"><span class="chip {sev}">{_esc(sev)}</span>Written by the copilot for {_esc(report['cockpit']['team'])}</div></header>
<main>
<section><h2>What happened</h2><p class="summary">{_esc(event['summary'])}</p></section>
<section class="meta">{meta}</section>
<section><h2>Measured</h2><ul>{facts}</ul>{f'<ul>{obs}</ul>' if obs else ''}</section>
<section><h2>Signal</h2>{charts}</section>
<section><h2>Likely causes</h2><ul>{causes}</ul></section>
<section><h2>Recommended next steps</h2><ul>{actions}</ul></section>
<section><h2>Sensor setup check</h2>{setup_html}</section>
{_datasheet_html(report.get("datasheet"))}
<section><h2>Timeline (15 minutes before)</h2><table>{timeline or '<tr><td colspan=2 class=muted>No other alerts.</td></tr>'}</table></section>
<section><h2>Ticket text</h2><pre id="ticket">{_esc(report['ticket'])}</pre></section>
</main></div>
<script>function copyTicket(b){{const t=document.getElementById('ticket').innerText;
(navigator.clipboard?navigator.clipboard.writeText(t):Promise.reject()).then(()=>{{b.textContent='Copied';}}).catch(()=>{{
const r=document.createRange();r.selectNodeContents(document.getElementById('ticket'));const s=getSelection();s.removeAllRanges();s.addRange(r);b.textContent='Selected: press Ctrl+C';}});}}</script>
</body></html>"""
