# Shared contract

What the backend, the cockpit, the phones and the voice model exchange. The backend produces these shapes; the
TypeScript types are in `frontend/src/types.ts`.

Positions are in the signal unit, µm, counted from the start of the sensor's measuring range. A sensor with a
`display` (the optoNCDT 5500 is shown in mm) is converted on screen only.

## WebSocket `/ws/cockpit`

First message after connecting:

```json
{"type": "hello", "config": {"title": "...", "team": "Mega Mind", "sensors": [...], "voice": {"enabled": true}},
 "events": [...], "status": {...}}
```

Live samples, about 50 points per second per sensor, thinned so peaks survive. `null` is an invalid reading.

```json
{"type": "samples", "sensor": "s1", "t0": 1764250000.0, "dt": 0.02, "unit": "µm", "values": [8001.2, 8000.9, null]}
```

Status once per second:

```json
{"type": "status", "t": 1764250001.0, "uptime_s": 120, "clients": {"cockpit": 1, "phone": 7},
 "sensors": {"s1": {"latest": 8001.2, "mean_1s": 8001.0, "noise": 0.15, "state": "ok", "active": [],
                    "source": {"type": "ild5500", "error": null}, "live": {...}, "part": {...}, "station": {...},
                    "read_error": null}}}
```

`state` is `ok`, `warning` or `critical`; `active` lists the alert kinds that are on.

`live` is the connection of the sensor:

```json
"live": {"kind": "ild5500", "state": "streaming", "message": "", "host": "169.254.168.150", "port": 1024,
         "rate_hz": 20000, "article": 4120118, "serial": 1425060077, "samples": 181232, "invalid_percent": 0.0,
         "last_status": null, "invalid_reasons": null, "reference": "range_start", "reference_mm": 0.0,
         "intensity_percent": null, "temperature_c": null}
```

`state` is `offline` (cannot connect; `message` says why), `no_data` (connected, nothing arrives), `streaming` or
`invalid` (readings arrive but most are invalid). A MEDAQLib source (`kind: medaqlib`) also sends `model` and
`interface`, and `host` reads like "ILD5500 via TCP/IP 169.254.168.150". A live sensor never falls back to simulated
data. A sensor whose connection cannot even be set up reports `state: offline` with the reason.

Events (also sent to phones):

```json
{"type": "event", "id": "ev-12345-7", "sensor": "s1", "kind": "wrong_part", "severity": "warning",
 "t": 1764250123.4, "numbers": {...}, "forecast_min": null, "related_sensors": [],
 "summary": "Different part detected on ILD5500: it reads 3.4981 mm, but the taught part reads 1.9981 mm ± 0.1000 mm. ..."}
```

| `kind` | Raised by | `numbers` |
| --- | --- | --- |
| `out_of_range` | the alert rules: the target left the measuring range (critical outside the extended range) | `value`, the zone, limits |
| `invalid` | the alert rules: too many invalid readings in one second | `invalid_percent`, `reasons`, `main_reason` |
| `wrong_part` | the part check, once per foreign part | `value`, `reference`, `half_width`, `deviation`, `parts`, `unit`; with the empty plate taught also `height`, `reference_height` and `stacked` when it is about twice the taught part |
| `jam` | the quality station, once per part, while it is still there | `stay_s`, `usual_stay_s`, `part` |
| `double_feed` | the quality station | `gap_s`, `usual_gap_s`, `part` |
| `trend` | the quality station, after a good part (critical within 10 parts) | `slope_um_per_part`, `parts_to_limit`, `trend_parts`, `r2` |

Every part that leaves the sensor, and every new run:

```json
{"type": "part", "sensor": "s1", "station": {...},
 "part": {"number": 12, "sensor": "s1", "t_start": 1764250100.2, "t_end": 1764250102.6, "dwell_s": 2.4,
          "gap_s": 1.0, "position_um": 2012.4, "height_um": 5988.9, "deviation_um": 14.3, "verdict": "good",
          "flags": [], "position": "2.0124 mm", "height": "5.9889 mm"}}
```

`part` is `null` after a new run. `flags` can hold `jam`, `double_feed` and `unsteady` (the part never lay still).

A report created by a tool call: `{"type": "report", "id", "title"}`.

## WebSocket `/ws/phone`

`hello` with `title`, `team`, `sensors` and the last events, then `event` and `part` messages. The phone page loads the
last parts from `GET /api/parts/{sensor}`.

## The part check: `part` in the status

```json
"part": {"supported": true, "state": "match", "steady": true, "position_um": 2001.2, "message": "…",
         "reference": {"center_um": 2001.9, "half_width_um": 100.0, "margin_um": 100.0, "parts": 1,
                       "low_um": 2001.9, "high_um": 2001.9, "taught_at": 1795811189.25},
         "empty_um": 8000.3, "height_um": 5998.4, "alerts": 0}
```

`state` is `no_reference`, `waiting`, `match`, `different` or `empty`. A sensor in another unit than µm has
`"supported": false`.

## The quality station: `station` in the status

```json
"station": {"ready": true, "message": "12 parts, 11 of them the taught part.", "parts": 12, "good": 11,
            "different": 1, "jams": 0, "double_feeds": 0, "yield_percent": 91.7, "per_minute": 18.5,
            "rhythm": {"known": true, "dwell_s": 2.4, "gap_s": 1.0},
            "trend": {"slope_um_per_part": 0.4, "r2": 0.12, "parts": 11, "parts_to_limit": null,
                      "direction": "lower", "clear": false},
            "current": {"number": 13, "since_s": 1.2, "position_um": 2003.0, "flags": []},
            "run_started": 1764250000.0, "last": {...}}
```

`ready` needs the empty plate and a good part taught. `rhythm.known` after three parts; `trend` after eight good
parts, `clear` when a straight line explains their heights (R² ≥ 0.6) and the change is at least 0.3 of the band's
half width. `current` is the part under the sensor right now. `GET /api/parts/{sensor}` adds `records`, newest first.

## REST

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/config` | Cockpit and sensor configuration |
| GET | `/api/status` | Same as the status message |
| GET | `/api/events?limit=50` | Recent events |
| GET | `/api/history?sensors=s1&window_s=300` | The signal for the live chart |
| GET | `/api/explore?chart=&sensors=s1&window_s=&group_by=` | A chart with its explanation steps, see below |
| GET | `/api/insights?view=timeseries&sensors=s1&window_s=` | Explanation steps for the live chart |
| GET | `/api/part/{sensor}` | The part check |
| POST | `/api/part/{sensor}/learn` | `{"kind": "part" \| "empty", "add": false}`: teach from the last second; 409 when not steady |
| POST | `/api/part/{sensor}/margin` | `{"margin_um": 100}`, 10 to 5000 µm |
| POST | `/api/part/{sensor}/forget` | `{"what": "all" \| "part" \| "empty"}` |
| GET | `/api/parts/{sensor}?limit=100` | The quality station with the last parts |
| GET | `/api/parts/{sensor}/csv` | The part log as a CSV for Excel (semicolons, decimal commas) |
| POST | `/api/parts/{sensor}/reset` | A new run: the part log, the counters and the rhythm start from zero |
| POST | `/api/tools/get_data`, `/api/tools/analyze` | `{"sensor", "window_s"}` for the voice copilot |
| POST | `/api/tools/create_report` | `{"event_id"}` (optional, default the latest) |
| GET | `/api/reports/{event_id}` | A report as JSON (`latest` works too) |
| GET | `/report/{event_id}` | The same report as a printable page |
| GET, POST, DELETE | `/api/knowledge...` | The manuals: list, upload, file, delete, search |
| POST | `/api/realtime/session` | A short-lived voice key plus the session configuration |
| GET | `/api/auth/check` | Whether the token is right |

POST and DELETE endpoints need the header `X-Cockpit-Token: <COCKPIT_TOKEN>`, except `/api/tools/get_data`, `/api/tools/analyze` and `/api/tools/create_report`.

## Charts

`ChartSpec` puts a chart on the stage: `{"view", "sensors", "window_s"?, "group_by"?}`.

| `view` | Data | What it shows |
| --- | --- | --- |
| `timeseries` | `/api/history` | The live signal with the limits and the alerts |
| `histogram` | `/api/explore` | How the readings are spread, against the limits |
| `boxplot` | `/api/explore` | The spread per minute, 10 minutes or hour |
| `parts` | `/api/explore` | The height of every part in order: dots by verdict, the band of the taught part, the trend line |
| `part_counts` | `/api/explore` | Good and different parts per minute, 10 minutes or hour (stacked bars) |
| `part_heights` | `/api/explore` | How the part heights are spread (a histogram of parts, `per: "parts"`) |

For the part charts a missing `window_s` means the whole run.

Every chart result carries `insights`, the steps of its explanation. Each step is one sentence and the place on the
chart it is about; all numbers in the text are computed by the backend.

```json
{"text": "The band is the taught part: 6.004 mm high, give or take 0.100 mm. A dot inside it is a good part.",
 "anchor": {"type": "yspan", "y0": 5904.0, "y1": 6104.0, "label": "taught part"}}
```

| Anchor | Drawn as | Fields |
| --- | --- | --- |
| `x` | vertical line | `t` (Unix seconds) or `x`, `label` |
| `point` | pulsing dot | `t` or `x`, `y`, `label` |
| `xspan` | shaded band across x | `t0`/`t1` or `x0`/`x1`, `label` |
| `yspan` | shaded band across y | `y0`, `y1`, `label` |
| `category` | one box or bar | `index`, `label` |
| `line` | a straight line | `x0`, `y0`, `x1`, `y1`, `label` |

`anchor` is `null` for a step that is only said.

## The voice copilot's tools

The voice model calls these; the browser runs them and returns the result.

| Tool | Arguments | Returns |
| --- | --- | --- |
| `get_data` | `sensor`, `window_s` | latest, mean, min, max, noise, limits, state, the data sheet |
| `analyze` | `sensor`, `window_s` | the above plus observations (zone, invalid share, part check, quality station, trend) |
| `show_chart` | `view`, `sensors`, `window_s` | `{"ok", "view", "sensors"}` |
| `make_chart` | `chart`, `sensors`, `window_s`, `group_by` | the chart's findings |
| `explain_chart` | | walks through the front chart |
| `stage_control` | `action`: `back` or `dismiss` | `{"ok"}` |
| `get_part_check` | `sensor` | the part check |
| `get_parts` | `sensor` | the quality station with the last ten parts (heights as text) |
| `create_report` | `event_id` (optional) | id, title, url, likely causes, next steps |
| `search_manual` | `query` | passages from the manuals with their pages |

The rule for the voice model: it never says a number that did not come from a tool.
