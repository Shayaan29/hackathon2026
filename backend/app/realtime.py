"""OpenAI Realtime voice: short-lived keys, the copilot's instructions and its tools.

The browser never sees the real API key. It asks ``POST /api/realtime/session``
for a short-lived client secret, connects to OpenAI over WebRTC with it, and
then sends the ``session_updates`` returned here over the data channel.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .config import Settings

log = logging.getLogger("cockpit.realtime")

CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"
CALLS_URL = "https://api.openai.com/v1/realtime/calls"

ALERT_PREFIX = "[MONITORING ALERT]"


class RealtimeError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


def instructions(settings: Settings) -> str:
    sensor_lines = "\n".join(
        f"- {s.name} (id {s.id}): {s.model}, unit {s.unit}, tolerance {s.tolerance.low:g} to "
        f"{s.tolerance.high:g} {s.unit}, adjustable settings: {', '.join(s.settings) or 'none'}"
        for s in settings.sensors
    )
    language = "German" if settings.cockpit.language.lower().startswith("de") else "English"
    name = settings.cockpit.assistant_name
    return f"""You are {name}, the voice copilot of a sensor cockpit that monitors Micro-Epsilon precision displacement sensors on a production line. The operator talks to you while working at the machine.

Sensors:
{sensor_lines}

How you speak:
- You are heard, not read. One or two short sentences per answer. No lists, no markdown.
- Answer in the language the operator uses. Start in {language}.
- Round numbers for speech: one decimal for micrometres, whole numbers for hertz and minutes.
- Sound calm and precise, like an experienced test engineer.

Hard rules:
- Never state a measurement, frequency, rate, forecast or setting you did not get from a tool in this conversation. If you need a number, call get_data or analyze first.
- When the operator wants to see something, call show_chart with the matching view, then say in one sentence what it shows.
- change_setting needs the operator's explicit yes to that exact change. Ask first, for example "Shall I set averaging on sensor one to 64?". Only after a clear yes call it with confirmed set to true.
- Messages that start with {ALERT_PREFIX} come from the monitoring system, not from the operator. Announce the alert in one or two sentences using only the numbers in it, then offer to show it. Do not call a tool for an alert unless the operator asks for more.
- If a question has nothing to do with the sensors or this cockpit, say briefly that you only handle the sensor cockpit.

Chart views for show_chart:
- timeseries: one sensor over time with its tolerance band. Default window 300 seconds.
- compare: several sensors on one time axis, to see whether they moved together.
- spectrum: frequency content. Use it for vibration questions or "is the sensor broken".
- drift: slow trend with a forecast to the tolerance limit.
Use stage_control with "back" to bring back the previous chart and "dismiss" to clear the current one.

Diagnosis hints (say "likely" or "usually", never certain):
- One clean frequency peak usually means mechanical vibration, not a sensor fault.
- The same event on several sensors at the same moment points to the fixture or the table, not one sensor.
- A slow, steady drift is often thermal.
- Invalid readings usually mean the target is out of range, tilted, or too shiny or too dark for the sensor.
- Higher averaging lowers noise but makes the sensor react more slowly."""


def tools(settings: Settings) -> list[dict[str, Any]]:
    sensor_ids = [s.id for s in settings.sensors]
    setting_names = sorted({name for s in settings.sensors for name in s.settings})
    sensor_param = {"type": "string",
                    "description": f"Sensor id ({', '.join(sensor_ids)}) or name, for example 'Sensor 1'."}
    return [
        {
            "type": "function",
            "name": "get_data",
            "description": "Current reading of one sensor: latest value, mean, min, max, noise, "
                           "tolerance, active alerts and settings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sensor": sensor_param,
                    "window_s": {"type": "number", "description": "Seconds to summarise, default 10."},
                },
                "required": ["sensor"],
            },
        },
        {
            "type": "function",
            "name": "analyze",
            "description": "Diagnosis of one sensor: last vibration with its frequency and which other "
                           "sensors saw it, drift rate with minutes to the tolerance limit, invalid "
                           "readings, recent events and plain-language observations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sensor": sensor_param,
                    "window_s": {"type": "number", "description": "Seconds to look back, default 60."},
                },
                "required": ["sensor"],
            },
        },
        {
            "type": "function",
            "name": "show_chart",
            "description": "Show a chart on the cockpit's main screen. The previous chart moves back "
                           "into the history stack.",
            "parameters": {
                "type": "object",
                "properties": {
                    "view": {"type": "string", "enum": ["timeseries", "compare", "spectrum", "drift"]},
                    "sensors": {"type": "array", "items": {"type": "string"},
                                "description": "Sensor ids. One for timeseries, spectrum and drift; "
                                               "two or more for compare."},
                    "window_s": {"type": "number",
                                 "description": "Time window in seconds (timeseries/compare/drift)."},
                },
                "required": ["view"],
            },
        },
        {
            "type": "function",
            "name": "stage_control",
            "description": "Navigate the chart stack: 'back' shows the previous chart again, "
                           "'dismiss' clears the current chart.",
            "parameters": {
                "type": "object",
                "properties": {"action": {"type": "string", "enum": ["back", "dismiss"]}},
                "required": ["action"],
            },
        },
        {
            "type": "function",
            "name": "change_setting",
            "description": "Change a sensor setting. Only call after the operator explicitly confirmed "
                           "this exact change.",
            "parameters": {
                "type": "object",
                "properties": {
                    "sensor": sensor_param,
                    "name": {"type": "string", "enum": setting_names},
                    "value": {"type": "string", "description": "New value, for example '64'."},
                    "confirmed": {"type": "boolean",
                                  "description": "True only if the operator said yes to this change."},
                },
                "required": ["sensor", "name", "value", "confirmed"],
            },
        },
    ]


def session_updates(settings: Settings) -> list[dict[str, Any]]:
    """Session configuration the browser sends after connecting.

    Split in two so that an optional feature (input transcription) can never
    take the essential configuration down with it if the API rejects it.
    """
    essential = {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "instructions": instructions(settings),
            "tools": tools(settings),
            "tool_choice": "auto",
            "audio": {"input": {"turn_detection": None}},
        },
    }
    transcription = {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "audio": {"input": {"transcription": {"model": settings.transcribe_model}}},
        },
    }
    return [essential, transcription]


async def create_client_secret(settings: Settings,
                               client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    if not settings.openai_api_key:
        raise RealtimeError("Voice is off: set OPENAI_API_KEY in backend/.env", status_code=503)
    body = {
        "session": {
            "type": "realtime",
            "model": settings.realtime_model,
            "instructions": instructions(settings),
            "audio": {"output": {"voice": settings.realtime_voice}},
        }
    }
    headers = {"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"}
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=15.0)
    try:
        response = await client.post(CLIENT_SECRETS_URL, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise RealtimeError(f"Could not reach OpenAI: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()
    if response.status_code >= 400:
        try:
            detail = response.json().get("error", {}).get("message", response.text)
        except ValueError:
            detail = response.text
        log.error("client secret request failed (%s): %s", response.status_code, detail)
        raise RealtimeError(f"OpenAI refused the session ({response.status_code}): {detail}")
    data = response.json()
    value = data.get("value") or (data.get("client_secret") or {}).get("value")
    if not value:
        raise RealtimeError("OpenAI returned no client secret")
    return {
        "client_secret": value,
        "expires_at": data.get("expires_at"),
        "model": settings.realtime_model,
        "calls_url": CALLS_URL,
        "session_updates": session_updates(settings),
        "alert_prefix": ALERT_PREFIX,
    }
