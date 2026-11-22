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
| POST | `/api/demo/{tap,vibration,drift,drift_stop,noise,dropout,reset}` | Simulator events, token required |

Token-protected endpoints expect the header `X-Cockpit-Token: <COCKPIT_TOKEN>`.

## Chart spec

What `show_chart` puts on the stage. `view` is one of `timeseries`,
`compare`, `spectrum`, `drift`.

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

The rule for the voice model: it never says a number that did not come from a tool.
