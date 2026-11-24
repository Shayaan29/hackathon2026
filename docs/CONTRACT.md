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
                    "read_error": null, "settings": {"averaging": 16}}}}
```

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
| `investigate_incident` | backend (cockpit opens the incident) | `incident_id` (optional, default the first) | `{"ok", "speak_rule", "incident"}`, or `{"ok": false, "error"}` |

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

Errors: 422 for a file or a mapping that cannot be read, 409 while an analysis
runs, 404 for an unknown incident id.

The chart the cockpit shows for an incident is the chart view `incident`, with
its `event_id`: `{"view": "incident", "sensors": ["s2"], "event_id": "INC-002"}`.
