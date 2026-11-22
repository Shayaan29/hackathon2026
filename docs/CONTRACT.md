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
