"""What the AI copilot can read about the incidents of the loaded recording.

Both functions return plain data. The copilot may only speak the numbers that are
in the result it received, and the result says so.
"""

from __future__ import annotations

from typing import Any

from .engine import Engine

SPEAK_RULE = ("Speak only numbers and times that appear in this result. Give the explanation in order. "
              "Call the checks what they are: checks to carry out, not a diagnosis.")


def list_incidents(engine: Engine, limit: int = 12) -> dict[str, Any]:
    store = engine.incidents
    if store.stats is None:
        return {"ok": True, "available": False, "incidents": [],
                "message": "No recording is analysed yet. Load a recording first."}
    rows = store.rows()
    return {"ok": True, "available": True, "total": len(rows), "incidents": rows[:max(1, min(limit, 50))],
            "recording": engine.recording_public().get("name")}


def investigate(engine: Engine, incident_id: str | None = None) -> dict[str, Any]:
    store = engine.incidents
    if store.stats is None:
        return {"ok": False, "error": "No recording is analysed yet. Load a recording first."}
    incident = store.get(incident_id) if incident_id else (store.items[0] if store.items else None)
    if incident is None:
        known = ", ".join(item["id"] for item in store.items[:20]) or "none"
        return {"ok": False, "error": f"no incident {incident_id!r}; known incidents: {known}"}
    return {
        "ok": True,
        "speak_rule": SPEAK_RULE,
        "incident": {
            "id": incident["id"], "title": incident["title"], "kind": incident["kind"],
            "kinds": incident["kinds"], "severity": incident["severity"], "status": incident["status"],
            "time": incident["t_text"], "sensor": incident["sensor_name"], "related": incident["related"],
            "summary": incident["summary"], "numbers": incident["numbers"],
            "checks": [{"label": c["label"], "value": c["value"], "result": c["status"], "detail": c["detail"]}
                       for c in incident["checks"]],
            "explanation": incident["explanation"],
            "suggested_checks": [{"check": s["text"], "why": s["reason"]} for s in incident["suggestions"]],
            "location": incident["location"],
            "not_known_from_file": incident["unknown"],
            "highlight": incident["highlight"],
        },
    }
