# Shared contract

What the backend, the cockpit, the phones and the voice model exchange. Code
against these shapes; the backend already produces them.

## WebSocket `/ws/cockpit`

First message after connecting:

```json
{"type": "hello", "config": {"title": "...", "sensors": [...], "voice": {"enabled": true, "model": "gpt-realtime-mini"}},
 "events": [...], "status": {...}}
```

Live samples, about 50 points per second per sensor, thinned so peaks survive
(each 40 ms bucket sends its min and max in time order). `null` is an invalid
reading.

```json
{"type": "samples", "sensor": "s1", "t0": 1764250000.0, "dt": 0.02, "unit": "µm", "values": [0.41, -0.62, null]}
```

Status once per second:

```json
{"type": "status", "t": 1764250001.0, "uptime_s": 120, "clients": {"cockpit": 1, "phone": 7},
 "sensors": {"s1": {"latest": 0.42, "mean_1s": 0.1, "noise": 0.8, "noise_ratio": 1.0, "state": "ok",
                    "active": [], "source": {"type": "simulator", "fallback": false, "error": null},
                    "live": null, "read_error": null, "settings": {"averaging": 16}}}}
```

`live` is `null` for simulated data, recordings and files. For a real sensor (source type `ild5500`, the optoNCDT
5500 over Ethernet) it holds the connection state:

```json
"live": {"kind": "ild5500", "state": "streaming", "message": "", "host": "169.254.168.150", "port": 1024,
         "rate_hz": 20000, "article": 4120118, "serial": 12345678, "samples": 181232, "invalid_percent": 0.0,
         "last_status": null, "invalid_reasons": null, "reference": "range_start", "reference_mm": 30.0,
         "intensity_percent": null, "temperature_c": null}
```

`state` is `offline` (cannot connect; `message` says why in plain words), `no_data` (connected, nothing arrives),
`streaming` (valid readings arrive) or `invalid` (readings arrive but most are invalid; `last_status` is the sensor's
own status text, for example "no peak"). `rate_hz` is the rate measured from the stream, `reference` says what is
zero (`range_start`, `auto`, `mid` or `fixed`), `reference_mm` the distance taken as zero, `invalid_reasons` the share
of each reason among the invalid readings of the last two seconds (for example `{"no_peak": 0.9, "after_range": 0.1}`; the keys are `no_peak`, `before_range`, `after_range`,
`cannot_evaluate`, `peak_too_wide`, `laser_off` and `other`; `null` when there were none) and `intensity_percent` and `temperature_c` are filled when the sensor sends those signals.
A live sensor never falls back to simulated data: when it is not connected no new samples arrive and
`live.state` says why. Readings the sensor itself marks as invalid (no target, out of range) arrive as `null`.

Events (also sent to phones). `kind` is one of `vibration`, `noise`, `drift`,
`out_of_range`, `invalid`; `severity` is `warning` or `critical`.

```json
{"type": "event", "id": "ev-12345-7", "sensor": "s2", "kind": "drift", "severity": "warning", "t": 1764250123.4,
 "numbers": {"rate_per_min": 6.1, "current": 2.3, "r2": 0.99, "unit": "µm"},
 "forecast_min": 6.2, "related_sensors": [],
 "summary": "Sensor 2 is drifting +6.1 µm per minute; at this rate it leaves tolerance in about 6 minutes."}
```

Setting changes:

```json
{"type": "setting_changed", "sensor": "s1", "name": "averaging", "old": 16, "new": 64, "t": 1764250200.0}
```

## WebSocket `/ws/phone`

`hello` with `title`, `team`, `sensors` and the last five events, then events only.

## REST

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/config` | Cockpit and sensor configuration |
| GET | `/api/status` | Same as the status message |
| GET | `/api/events?limit=50` | Recent events |
| GET | `/api/history?sensors=s1,s2&window_s=300` | Timeseries and compare views |
| GET | `/api/spectrum?sensor=s1&window_s=2&event_id=` | Spectrum, centred on the last vibration |
| GET | `/api/drift?sensor=s2&window_s=900` | Per-second averages, trend and forecast |
| POST | `/api/tools/get_data` | `{"sensor", "window_s"}` |
| POST | `/api/tools/analyze` | `{"sensor", "window_s"}` |
| POST | `/api/tools/change_setting` | `{"sensor", "name", "value", "confirmed"}`, token required |
| POST | `/api/realtime/session` | Short-lived voice key plus session configuration, token required |
| POST | `/api/demo/{tap,vibration,drift,drift_stop,noise,dropout,dent,reset}` | Simulator events, token required |

Token-protected endpoints expect the header `X-Cockpit-Token: <COCKPIT_TOKEN>`.

## Chart spec

What `show_chart` puts on the stage. `view` is one of `timeseries`,
`compare`, `spectrum`, `drift`, and since the chart update also `histogram`,
`boxplot`, `scatter`, `bar` (with `group_by` and `metric`) and `shift`.

```json
{"view": "spectrum", "sensors": ["s1"], "window_s": 300, "event_id": "ev-12345-6"}
```

## AI tools

The voice model calls these; the browser runs them and returns the result.

| Tool | Runs | Arguments | Returns |
| --- | --- | --- | --- |
| `get_data` | backend | `sensor`, `window_s` | latest, mean, min, max, noise, tolerance, state, settings |
| `analyze` | backend | `sensor`, `window_s` | the above plus last vibration, drift trend, minutes to limit, observations |
| `show_chart` | cockpit | `view`, `sensors`, `window_s` | `{"ok", "view", "sensors"}` |
| `stage_control` | cockpit | `action`: `back` or `dismiss` | `{"ok"}` |
| `change_setting` | backend | `sensor`, `name`, `value`, `confirmed` | old and new value, or an error |
| `suggest_settings` | backend (cockpit opens the setup check) | `sensor` | measured noise, proposal, reasons, expected noise |
| `create_report` | backend (cockpit opens the report) | `event_id` (optional, default latest) | id, title, url, likely causes, next steps |

The rule for the voice model: it never says a number that did not come from a tool.


## Added for the setup assistant, reports and shakes

| Endpoint | Purpose |
| --- | --- |
| `GET /api/assistant/{sensor}` | Measures the signal and proposes settings; includes a before/after preview of the last 2 s |
| `POST /api/assistant/{sensor}/apply` | Applies the proposal (needs `X-Cockpit-Token`) |
| `GET /api/reports/{event_id}` | Incident report as JSON (`latest` works too) |
| `GET /report/{event_id}` | The same report as a printable page; `?embed=1` hides its own buttons |
| `POST /api/demo/misconfigure` | Demo: sets averaging on a sensor to 2 so it is noisy |

| Message | Direction | Fields |
| --- | --- | --- |
| `{"type": "shake", "name", "intensity"}` | phone to server | intensity 0 to 3 |
| `{"type": "shake_ack", "ok", "reason"?, "retry_s"?}` | server to that phone | `reason`: `busy` or `unsupported` |
| `{"type": "shake", "name", "sensor", "intensity", "t"}` | server to cockpits and phones | after a knock was applied |
| `{"type": "report", "id", "title"}` | server to cockpits | a report was created by a tool call |


## Added for charts, explanations and the shift timeline

| Endpoint | Purpose |
| --- | --- |
| `GET /api/explore?chart=&sensors=s1,s2&window_s=&group_by=&metric=` | A chart the copilot picked, with its explanation steps. `chart`: `histogram`, `boxplot`, `scatter`, `bar`. `group_by` (box plot): `minute`, `10min`, `hour`, `sensor`. `metric` (bar): `noise`, `mean`, `range`, `events`. Unknown chart: 400 |
| `GET /api/insights?view=&sensors=&window_s=&event_id=` | Explanation steps for the live views `timeseries`, `compare`, `spectrum`, `drift` |
| `GET /api/shift?sensor=s1&points=1800` | The whole shift of one sensor, its alerts and a guided tour (`insights`, `shift_start`) |
| `GET /api/range?sensor=s1&t0=&t1=&points=1600` | The finest data for a time window, see below |
| `POST /api/demo/dent` | Demo: a 62 µm dent in the part under the sensor; it comes round once per turn |

`/api/config` also returns `part_rpm`, how fast the measured part turns. The
twin uses it to paint each reading where it was taken on the part.

### Explanation steps

Every chart result carries `insights`: the steps, in order. Each step is one
spoken sentence and the place on the chart it is about. All numbers in the
text are computed by the backend.

```json
{"text": "These are 150,000 readings of Sensor 1 from the last 5 minutes. Nine in ten fall between -1.4 and +1.5 µm.",
 "anchor": {"type": "xspan", "x0": -1.4, "x1": 1.5, "label": "9 in 10 readings"}}
```

| Anchor | Drawn as | Fields |
| --- | --- | --- |
| `x` | vertical line | `t` (Unix seconds) or `x`, `label`, `sensor`? |
| `point` | pulsing dot | `t` or `x`, `y`, `label` |
| `xspan` | shaded band across x | `t0`/`t1` or `x0`/`x1`, `label` |
| `yspan` | shaded band across y | `y0`, `y1`, `label` |
| `grid` | the whole plot area | `label` |
| `category` | one box or bar | `index`, `label` |
| `line` | a straight line | `x0`, `y0`, `x1`, `y1`, `label` |
| `zoom` | (timeline only) flies to a window | `t0`, `t1`, `label`? |

`anchor` can be `null` for a step that is only said.

### Shift timeline data

`/api/range` picks the finest data that covers the window: `full` (every
reading, the last `fast_buffer_s`), `snapshot` (full rate, saved from 3 s
before to 3.5 s after every alert) or `seconds` (min, mean and max of every
second, for the whole shift). When there are more readings than `points`, each
slice keeps its average in `v` and its lowest and highest reading in `lo` and
`hi`, so a spike never disappears. Not thinned, `v`, `lo` and `hi` are the same.

```json
{"view": "shift", "sensor": "s1", "t0": 1764250000.0, "t1": 1764250002.0, "t_now": 1764278800.0,
 "t": [1764250000.0, 1764250000.002], "v": [0.41, -0.62], "lo": [0.41, -0.62], "hi": [0.41, -0.62],
 "source": "snapshot", "resolution_s": 0.002, "events": [...]}
```

`events` holds only the alerts that involve this sensor (its own, and knocks
it shared). With the simulator the shift starts with eight hours of made-up
history (`"historic": true` on those events); real sources start empty.

### Tools

| Tool | Runs | Arguments | Returns |
| --- | --- | --- | --- |
| `make_chart` | backend data, cockpit shows it | `chart`, `sensors`, `window_s`, `group_by`, `metric` | `{"ok", "chart", "findings"}`: the step texts |
| `explain_chart` | cockpit | none | `{"ok", "steps"}` for the chart in front; the cockpit reveals each marker while the copilot speaks |
| `show_shift` | cockpit | `sensor`, `event_id` (optional) | `{"ok", "incidents", "overview"}`; with `event_id` it flies to that incident |
| `list_incidents` | backend | none | `{"ok", "available", "total", "incidents", "recording"}`, see below |
| `investigate_incident` | backend (cockpit opens the incident) | `incident_id` (optional, default the first) | `{"ok", "speak_rule", "incident", "recommendations"}`, or `{"ok": false, "error"}` |
| `search_manual` | backend (cockpit shows the passages) | `query` | `{"ok", "speak_rule", "query", "documents", "passages", "message"}`, see the tool table below. Needs the cockpit token |

## Added for sensors with a data sheet (measuring range, millimetres)

A sensor whose `model` is in the catalogue (`backend/app/sensor_specs.py`, today the optoNCDT ILD5500-10, -25,
-50, -100 and -200) has a data sheet. Every value in it carries the page of the operating instructions it comes from.
The signal itself stays in the unit of the sensor (`µm`, counted from the start of the measuring range); only the
wording changes.

`/api/config` sensors gain:

| Field | Meaning |
| --- | --- |
| `tolerance_kind` | `part` (the limits are a workpiece tolerance typed into the YAML) or `measuring_range` (the limits are the measuring range of the model) |
| `limit_word` | `tolerance` or `measuring range`, the word the interface and the reports use for the limits |
| `display` | `{"unit": "mm", "scale": 0.001, "decimals": 4}` or `null`. A position `v` in the payload is shown as `v * scale` in `unit`. Rates (noise, vibration, drift per minute) stay in µm |
| `spec` | `null` without a data sheet, otherwise the object below |

```json
"spec": {"family": "optoNCDT 5500", "model": "ILD5500-10", "title": "optoNCDT ILD5500-10",
         "measuring_range_mm": 10, "start_mm": 30, "mid_mm": 35, "end_mm": 40,
         "zones_um": {"ext_low": -2500.0, "low": 0.0, "mid": 5000.0, "high": 10000.0, "ext_high": 12500.0},
         "linearity_um": 1.5, "repeatability_um": 0.008, "temperature_um_per_k": 0.8,
         "manual": "optoNCDT 5500 operating instructions"}
```

`zones_um` are positions from the start of the measuring range: between `low` and `high` the target is in the
measuring range (state LED green), between `ext_low` and `low` or `high` and `ext_high` it is still measured but in the
extended range (LED flashing green), beyond that it is outside (LED red). `start_mm`, `mid_mm` and `end_mm` are the
distances from the sensor housing. For `tolerance_kind` `measuring_range` the sensor's `tolerance` is `low` 0 and
`high` the measuring range in µm, and `nominal` is its middle.

The results of `/api/history` (each series), `/api/drift`, `/api/range`, `/api/shift`, `/api/explore` (histogram,
box plot, scatter and bar) and the report charts carry the `display` of their sensor(s), and `limit_word` where a text
needs it (`display` is `null` for a sensor shown in its own unit). A histogram series has `capability: false` for a
measuring range, because Cp and Cpk describe a part tolerance. The explanation steps and the shift tour say
"measuring range" instead of "tolerance" for such a sensor.

An `out_of_range` event of such a sensor has extra `numbers`: `zone` (`before`, `after` or `outside`), `led`, `range`
(`"0 to 10 mm"`) and `extended_range` (`"-2.5 to 12.5 mm"`). Its summary names where the target is and which range
it left.

`GET /api/assistant/{sensor}` for a sensor with a data sheet and no `settings` block cannot propose values, because
the cockpit does not change the sensor's own settings. It answers with `"status": "advice"` (besides `ok` and
`improve`), `basis` (what the noise was compared with, for example `"Data sheet linearity (±1.5 µm)"`), `proposal` an
empty list and `reasons` the things to check, each with its manual page.

`GET /api/reports/{event_id}` gains `datasheet`: `{"title", "manual", "rows": [{"label", "value", "page"}]}` (or `null`),
and each entry of `sensors` gains `display` and `limit_word`. The title says "Out of measuring range" for such a sensor,
"Out of tolerance" for a part tolerance. The ticket text names the sensor, its range and linearity with the manual page.


## Added for recordings and incidents

A file loaded through **Load file** in the top bar, or through the endpoints
below, is read on the server. The whole file is checked with the same rules as
the live sensors. Each incident carries its evidence, the checks it ran, an
explanation, checks to carry out, what the file does not tell, and a snapshot of
the data around it. Writes need `X-Cockpit-Token`; reads are open.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/recording/inspect` | The raw file as the body, with `Content-Type: application/octet-stream` and its name URL-encoded in `X-File-Name`. Returns `layout`, `mapping`, `preview`, `warnings` and `file`. 422 if the file cannot be read |
| POST | `/api/recording/analyse` | `{"mapping": {...}, "rate_hz": null}`. Analyses the file from the last inspect and returns 202. 409 while another analysis runs. The top-level `rate_hz` is the analysis rate (default 500 Hz). The file's own sample rate goes into `mapping.rate_hz` |
| GET | `/api/recording/status` | `state` (`idle`, `running`, `done` or `error`), `progress` (0 to 1), `message`, `error`, `result` (`stats`, `warnings`, `source`), `recording` and `playbook_error` |
| POST | `/api/recording/restore` | Puts the simulator back and clears the incidents |
| GET | `/api/incidents` | `incidents` (short rows), `stats`, `source`, `warnings` and `recording` |
| GET | `/api/incidents/{id}` | One full incident, see below. 404 if the id is unknown |
| POST | `/api/incidents/{id}/acknowledge` | Sets `status` to `acknowledged` and returns the short row |
| GET | `/api/incidents/{id}/csv` | The data around the incident: semicolons, decimal commas, `time_s` counted from the start of the recording |
| GET | `/api/incidents/{id}/report` | The incident report as HTML. For the demo file it says "Simulated demo data." |
| GET | `/report/incident/{id}` | The same report as a page of its own |
| POST | `/api/tools/list_incidents` | The copilot's tool, see below |
| POST | `/api/tools/investigate_incident` | The copilot's tool, see below |
| GET | `/api/incidents/{id}/recommendations` | What was already prepared, a recommendation set (below). `{"status": "none", "ai"}` if nothing yet. Never calls the model. 404 for an unknown id |
| POST | `/api/incidents/{id}/recommendations` | Token. `{"force": false}`. Drafts, checks and returns the recommendation set. Answers from the cache unless `force` |
| GET | `/api/knowledge` | The manuals: `documents` (name, kind, pages, passages, bytes), `passages`, `warnings`, `embeddings` (enabled, model, ready, error), `ai`, `model`, `prefetching`, `cached` |
| POST | `/api/knowledge/upload` | Token. The file as the body, its name in `X-File-Name` (URL encoded). PDF, `.txt` or `.md`. 422 with the reason if it cannot be read |
| DELETE | `/api/knowledge/{name}` | Token. Removes a manual |
| GET | `/api/knowledge/file/{name}` | The manual file itself, so a quote can link to `#page=N` |
| POST | `/api/knowledge/search` | Token. `{"query", "k"}`. `{"hits": [{doc, page, section, text, score, via}]}` |
| POST | `/api/tools/search_manual` | Token. The copilot's tool |

The sheet sends a mapping of this shape. `part_rpm` 0 means unknown. A tolerance
has `tolerance_source` `placeholder` until its limits are typed, then `entered`.

```json
{"delimiter": ";", "decimal": ",", "data_line": 0, "time_index": 0, "time_unit": "s",
 "rate_hz": null, "part_rpm": 8.6,
 "sensors": [{"index": 1, "include": true, "name": "Sensor 1", "unit": "µm",
              "tolerance_low": -40, "tolerance_high": 40, "tolerance_source": "placeholder",
              "fixture": "recording", "zero": "start"}]}
```

Status while a recording plays. The same object is in `/api/status` and in the
status message. `started_at` is the server time at which sample 0 started to play.
Restoring the simulator sends `{"type": "recording", "active": false}`.

```json
{"type": "recording", "active": true, "name": "demo_recording.csv", "rate_hz": 500, "duration_s": 130.0,
 "started_at": 1764250000.0, "incidents": 5, "rows": 65000, "hidden_columns": [],
 "source": {...}, "slots": [...]}
```

An incident in a list (`/api/incidents`, and the `incidents` of `list_incidents`):

```json
{"id": "INC-002", "title": "Drift on Sensor 2 at 01:07.248", "kind": "drift", "kinds": ["drift"],
 "severity": "warning", "status": "open", "t": 67.248, "t_text": "01:07.248", "sensor": "s2",
 "sensor_name": "Sensor 2", "related": [], "repeats": 1,
 "summary": "Sensor 2 is drifting +6.2 µm per minute; at this rate it leaves tolerance in about 6 minutes."}
```

`kind` is one of `vibration`, `noise`, `drift`, `out_of_range`, `invalid`. `status`
is `open` or `acknowledged`. `t` is seconds from the start of the recording.

One incident (`/api/incidents/{id}`) has the same fields, and in addition:

| Field | What it holds |
| --- | --- |
| `numbers` | The measured numbers the title and summary use |
| `forecast_min` | Minutes to the limit, when a drift is found |
| `highlight` | `{"from_s", "to_s"}`: the window the chart shades |
| `window` | `{"from_s", "to_s"}`: the data included in the snapshot |
| `checks` | `[{"label", "value", "status", "detail"}]`. `status` is `found`, `not_found`, `info` or `unknown`. `not_found` means the rule did not see the pattern, not an error |
| `explanation` | The explanation, one sentence per line, in order |
| `suggestions` | `[{"text", "reason"}]`: checks to carry out, from the playbook, each with its reason |
| `unknown` | What the file does not tell, one sentence per line |
| `location` | `sensor`, `sensor_name`, `twin_side`, `part_angle_deg` (null when the part speed is unknown), `turn`, `time_of_peak_s`, `twin_angle_deg` |
| `recurrence` | `count` and `median_interval_s` for the same kind on the same sensor |
| `snapshot` | `t0`, `rate_hz` and `sensors` (`id`, `name`, `unit`, `tolerance`, `values`). A `null` value is an invalid reading |
| `source` | The recording: name, rate, and the part speed it was analysed with |

Suggested checks come from `backend/config/playbook.yaml`: one list for each kind
of alert, plus `all`. Each entry has a `text`, a `reason` filled in with the
measured numbers, and optionally `needs_frequency: true`, which hides the check
when no clear frequency was found. A value the file does not give is printed as
"unknown", never as a guess.

The copilot's tools:

| Tool | Returns |
| --- | --- |
| `list_incidents` (no arguments) | `{"ok": true, "available": true, "total", "incidents" (up to 12 short rows), "recording"}`. `available` is false when no recording is analysed |
| `investigate_incident` (`incident_id` optional) | `{"ok": true, "speak_rule", "incident": {id, title, kind, kinds, severity, status, time, sensor, related, summary, numbers, checks, explanation, suggested_checks, location, not_known_from_file, highlight}}`. The `speak_rule` tells the model to speak only numbers that appear in the result, and to call the checks checks to carry out, not a diagnosis |
| `search_manual` (`query`) | `{"ok": true, "speak_rule", "query", "documents", "passages": [{id, uid, doc, page, section, text, score}], "message"}`. With no manual loaded: `{"ok": true, "documents": 0, "passages": [], "message"}`. With an empty query: `{"ok": false, "error"}`. The cockpit shows the passages as a "From the manuals" card |

A recommendation set, from `/api/incidents/{id}/recommendations`:

```json
{"status": "ai", "incident": "INC-001", "model": "gpt-5-mini", "note": "", "dropped": 1, "manuals": 2, "passages": 6,
 "seconds": 8.4, "generated": "13:02:11",
 "facts": [{"id": "F3", "text": "Dominant frequency: 26.0 Hz (one clear peak, ...)"}],
 "recommendations": [{"id": "R1", "action": "...", "why": "...", "basis": "manual", "facts": ["F3"],
                      "sources": [{"doc": "man--optoNCDT.pdf", "page": 12, "section": "", "quote": "..."}]}]}
```

`status` is `ai` (drafted by the model and checked) or `rules` (the fixed checks of `config/playbook.yaml`,
with `note` saying why). `basis` is `manual` (at least one verified quote), `data` (measurements only) or `rules`.
A draft is dropped, and counted in `dropped`, unless it cites a real fact, every number in it appears in the
measured facts or in its quote, every quote is word for word in the passage it names, and it does not state a cause
as certain. In the `investigate_incident` result, `recommendations` is `null` (with a `recommendations_note`) when the set is not
ready after 10 s, otherwise `{"speak_rule", "kind": "ai_checked_against_the_data" | "rule_based", "dropped_drafts",
"items": [{action, why, based_on: [fact text], sources: [{document, page, quote}]}]}`.

Errors: 422 for a file or a mapping that cannot be read, 409 while an analysis
runs, 404 for an unknown incident id.

The chart the cockpit shows for an incident is the chart view `incident`, with
its `event_id`: `{"view": "incident", "sensors": ["s2"], "event_id": "INC-002"}`.
