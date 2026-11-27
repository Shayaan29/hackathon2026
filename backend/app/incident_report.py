"""A printable report for one incident: what was measured, which checks ran, the
explanation, the suggested checks and what the file does not tell us.

The report is self-contained (inline SVG, no scripts, no external files), so it
can be printed, saved as PDF from the browser, or opened on a phone.
"""

from __future__ import annotations

import html
import time
from typing import Any

STATUS_TEXT = {"open": "Open", "acknowledged": "Acknowledged"}
STATUS_CLASS = {"found": "found", "not_found": "not-found", "info": "info", "unknown": "unknown"}
STATUS_LABEL = {"found": "Found", "not_found": "Not found", "info": "Context", "unknown": "Unknown"}

STYLE = """
:root { color-scheme: light; }
body { font: 15px/1.55 "Segoe UI", system-ui, sans-serif; color: #1d2430; margin: 0; background: #f5f6f8; }
main { max-width: 880px; margin: 0 auto; padding: 32px 24px 56px; background: #fff; min-height: 100vh; }
h1 { font-size: 26px; margin: 0 0 6px; }
h2 { font-size: 17px; margin: 30px 0 10px; padding-bottom: 6px; border-bottom: 1px solid #e3e7ee; }
.meta { color: #5b6573; margin: 0; }
.banner { background: #fff4d6; border: 1px solid #e8c55a; padding: 10px 14px; border-radius: 6px; margin: 16px 0; }
.severity-critical { color: #b42318; font-weight: 600; }
.severity-warning { color: #9a6700; font-weight: 600; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { text-align: left; padding: 7px 8px; border-bottom: 1px solid #edf0f4; vertical-align: top; }
th { color: #5b6573; font-weight: 600; font-size: 13px; }
.tag { display: inline-block; font-size: 12px; padding: 1px 8px; border-radius: 10px; }
.tag.found { background: #fde8e6; color: #b42318; }
.tag.not-found { background: #e6f4ea; color: #16733a; }
.tag.info { background: #eef2f7; color: #3b4758; }
.tag.unknown { background: #fff4d6; color: #7a5a00; }
ol li, ul li { margin-bottom: 8px; }
.reason { color: #5b6573; font-size: 14px; display: block; margin-top: 2px; }
.source { display: block; margin: 4px 0 0 12px; padding-left: 10px; border-left: 3px solid #0a84a6; color: #27323f; font-size: 14px; font-style: italic; }
.note { color: #5b6573; font-size: 14px; }
figure { margin: 14px 0; }
figcaption { font-size: 13px; color: #5b6573; margin-top: 2px; }
svg { width: 100%; height: auto; display: block; }
footer { margin-top: 36px; font-size: 12px; color: #7b8593; border-top: 1px solid #e3e7ee; padding-top: 12px; }
@media print { body { background: #fff; } main { padding: 0; } h2 { break-after: avoid; } figure { break-inside: avoid; } }
"""


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _panel(sensor: dict[str, Any], snap: dict[str, Any], highlight: dict[str, float], event_t: float) -> str:
    rate = snap["rate_hz"]
    t0 = snap["t0"]
    points = [(t0 + i / rate, v) for i, v in enumerate(sensor["values"]) if v is not None]
    name = _esc(sensor["name"])
    if len(points) < 2:
        return f"<p class='note'>{name}: no valid readings in this window.</p>"
    points = points[:: max(1, len(points) // 900)]
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, x1 = min(xs), max(xs)
    lo, hi = min(ys), max(ys)
    pad = max((hi - lo) * 0.12, 0.5)
    lo, hi = lo - pad, hi + pad
    width, height, left, right, top, bottom = 760, 170, 58, 14, 12, 30

    def X(x: float) -> float:
        return left + (x - x0) / max(x1 - x0, 1e-9) * (width - left - right)

    def Y(y: float) -> float:
        return top + (1 - (y - lo) / max(hi - lo, 1e-9)) * (height - top - bottom)

    parts = [f"<svg viewBox='0 0 {width} {height}' role='img' aria-label='{name} around the incident'>"]
    shade_from, shade_to = max(x0, highlight["from_s"]), min(x1, highlight["to_s"])
    if shade_to > shade_from:
        parts.append(f"<rect x='{X(shade_from):.1f}' y='{top}' width='{X(shade_to) - X(shade_from):.1f}' "
                     f"height='{height - top - bottom}' fill='#fde8e6'/>")
    tol = sensor.get("tolerance") or {}
    for limit in (tol.get("low"), tol.get("high")):
        if limit is not None and lo <= limit <= hi:
            parts.append(f"<line x1='{left}' x2='{width - right}' y1='{Y(limit):.1f}' y2='{Y(limit):.1f}' "
                         "stroke='#b42318' stroke-dasharray='5 4' stroke-width='1'/>")
            parts.append(f"<text x='{left - 6}' y='{Y(limit) + 4:.1f}' text-anchor='end' font-size='11' "
                         f"fill='#b42318'>{limit:+g}</text>")
    if x0 <= event_t <= x1:
        parts.append(f"<line x1='{X(event_t):.1f}' x2='{X(event_t):.1f}' y1='{top}' y2='{height - bottom}' "
                     "stroke='#1d2430' stroke-width='1.2'/>")
    line = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in points)
    parts.append(f"<polyline points='{line}' fill='none' stroke='#2563eb' stroke-width='1.4'/>")
    parts.append(f"<text x='{left - 6}' y='{top + 4}' text-anchor='end' font-size='11' fill='#5b6573'>{hi:.1f}</text>")
    parts.append(f"<text x='{left - 6}' y='{height - bottom}' text-anchor='end' font-size='11' "
                 f"fill='#5b6573'>{lo:.1f}</text>")
    parts.append(f"<text x='{left}' y='{height - 8}' font-size='11' fill='#5b6573'>{x0:.2f} s</text>")
    parts.append(f"<text x='{width - right}' y='{height - 8}' text-anchor='end' font-size='11' "
                 f"fill='#5b6573'>{x1:.2f} s</text>")
    parts.append("</svg>")
    unit = _esc(sensor.get("unit", ""))
    caption = (f"{name} in {unit}. Shaded: the time window the rule looked at. Solid line: the moment of the alert. "
               "Dashed red: tolerance limits.")
    return f"<figure>{''.join(parts)}<figcaption>{caption}</figcaption></figure>"


def _recommendations_html(result: dict[str, Any] | None) -> str:
    """The verified AI recommendations, when they have been prepared for this incident."""
    if not result or result.get("status") != "ai" or not result.get("recommendations"):
        return ""
    facts = {f["id"]: f["text"] for f in result.get("facts", [])}
    items = []
    for rec in result["recommendations"]:
        based = "; ".join(_esc(facts[i]) for i in rec.get("facts", []) if i in facts)
        quotes = "".join(
            f"<span class='source'>\u201c{_esc(src['quote'])}\u201d, {_esc(src['doc'])}"
            + (f", page {_esc(src['page'])}" if src.get("page") else "") + "</span>"
            for src in rec.get("sources", []))
        items.append(f"<li>{_esc(rec['action'])}<span class='reason'>Why: {_esc(rec['why'])}</span>"
                     + (f"<span class='reason'>Based on: {based}</span>" if based else "") + quotes + "</li>")
    dropped = int(result.get("dropped") or 0)
    dropped_text = (f" {dropped} draft{'s' if dropped != 1 else ''} failed the checks and "
                    f"{'were' if dropped != 1 else 'was'} left out." if dropped else "")
    return ("<h2>Recommended next steps</h2>"
            f"<p class='note'>Drafted by {_esc(result.get('model', ''))} from the measured numbers above and "
            f"{_esc(result.get('manuals', 0))} loaded manual(s). A step is kept only if its numbers come from the "
            f"data or the quote and its quote is word for word in the manual.{dropped_text} "
            "These are checks to carry out, not a diagnosis.</p>"
            f"<ol>{''.join(items)}</ol>")


def render_html(incident: dict[str, Any], team: str = "", title_prefix: str = "KI Sensor Cockpit",
                recommendations: dict[str, Any] | None = None) -> str:
    snap = incident["snapshot"]
    source = incident.get("source", {})
    status = STATUS_TEXT.get(incident["status"], incident["status"])
    simulated = any("SIMULATED" in line.upper() for line in source.get("metadata", []))
    banner = ("<div class='banner'><strong>Simulated demo data.</strong> The file is marked as generated for "
              "training. It is not a measurement of a real part.</div>") if simulated else ""

    checks = "".join(
        f"<tr><th scope='row'>{_esc(c['label'])}</th><td>{_esc(c['value'])}</td>"
        f"<td><span class='tag {STATUS_CLASS.get(c['status'], 'info')}'>{STATUS_LABEL.get(c['status'], '')}</span></td>"
        f"<td>{_esc(c.get('detail', ''))}</td></tr>"
        for c in incident["checks"])
    explanation = "".join(f"<li>{_esc(line)}</li>" for line in incident["explanation"])
    suggestions = "".join(
        f"<li>{_esc(s['text'])}<span class='reason'>Why: {_esc(s['reason'])}</span></li>"
        for s in incident["suggestions"]) or "<li>No suggested checks for this alert type.</li>"
    unknown = "".join(f"<li>{_esc(item)}</li>" for item in incident.get("unknown", [])) or "<li>Nothing relevant is missing.</li>"
    panels = "".join(
        f"<h3 style='font-size:14px;margin:14px 0 0'>{_esc(s['name'])}</h3>"
        + _panel(s, snap, incident["highlight"], incident["t"])
        for s in snap["sensors"])
    loc = incident["location"]
    if loc.get("part_angle_deg") is not None:
        location_text = (f"{loc['part_angle_deg']:.0f}° of turn {loc['turn']}, counted from the first sample of the "
                         "recording. The file has no once-per-turn marker, so 0° is not a physical mark on the part.")
    else:
        location_text = "The part speed was not entered, so the angle on the part is unknown."
    twin_side = loc.get("twin_side")
    twin_text = (f"{_esc(loc['sensor_name'])} is drawn on the {_esc(twin_side)} side of the 3D twin."
                 if twin_side else f"{_esc(loc['sensor_name'])} has no position in the 3D twin.")
    created = time.strftime("%Y-%m-%d %H:%M")
    rpm_text = (f"{source.get('part_rpm')} rpm (entered in the mapping)" if source.get("part_rpm")
                else "not entered")
    body = f"""
<main>
  <h1>{_esc(incident['title'])}</h1>
  <p class='meta'>Incident {_esc(incident['id'])} · recording {_esc(source.get('file', ''))} ·
     status {_esc(status)} · severity <span class='severity-{_esc(incident['severity'])}'>{_esc(incident['severity'])}</span></p>
  {banner}
  <h2>What happened</h2>
  <p>{_esc(incident['summary'])}</p>
  <h2>How it was found</h2>
  <ol>{explanation}</ol>
  <h2>Measurement</h2>
  {panels}
  <h2>Checks the rules ran</h2>
  <table><thead><tr><th>Check</th><th>Value</th><th>Result</th><th>Detail</th></tr></thead>
  <tbody>{checks}</tbody></table>
  <h2>Location</h2>
  <p>{location_text}</p>
  <p class='note'>{twin_text} Position on the part: {_esc(rpm_text)}.</p>
  {_recommendations_html(recommendations)}
  <h2>Suggested checks</h2>
  <p class='note'>These are checks to carry out, not a diagnosis. Each one shows the measured numbers behind it.</p>
  <ol>{suggestions}</ol>
  <h2>Not known from the file</h2>
  <ul>{unknown}</ul>
  <footer>
    {_esc(title_prefix)}{(' · ' + _esc(team)) if team else ''} · report created {created} ·
    file {_esc(source.get('file', ''))}, digest {_esc(source.get('digest', ''))}, analysed at {_esc(source.get('analysed_at', ''))},
    {_esc(source.get('rate_hz', ''))} Hz analysis rate, {_esc(source.get('rows', ''))} rows.
  </footer>
</main>"""
    return (f"<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{_esc(incident['id'])} · {_esc(incident['title'])}</title><style>{STYLE}</style></head>"
            f"<body>{body}</body></html>")
