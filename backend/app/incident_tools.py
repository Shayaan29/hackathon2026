"""What the AI copilot can read about the incidents of the loaded recording.

Both functions return plain data. The copilot may only speak the numbers that are
in the result it received, and the result says so.
"""

from __future__ import annotations

from typing import Any

from .engine import Engine
from .knowledge import KnowledgeBase

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


MANUAL_RULE = ("Answer only from these passages and name the document and page. If they do not answer the question, "
               "say the loaded manuals do not cover it. Speak only numbers that appear in the passages.")

RECOMMEND_RULE = ("Say the recommendations in the order given, one short sentence each, in your own words. When one has "
                  "a source, say the document and page. Speak only numbers that appear in this result. These are "
                  "checks to carry out, not a diagnosis.")


async def search_manual(kb: KnowledgeBase, query: str, limit: int = 4) -> dict[str, Any]:
    query = " ".join(str(query or "").split())
    if not query:
        return {"ok": False, "error": "Give a question or a few keywords to look up."}
    if not kb.documents:
        return {"ok": True, "documents": 0, "passages": [],
                "message": "No manual is loaded. Add manuals in the Manuals panel first."}
    hits = await kb.search(query, k=limit)
    return {"ok": True, "speak_rule": MANUAL_RULE, "query": query, "documents": len(kb.documents),
            "passages": [{"id": f"P{n}", **hit.passage.public(hit.score)} for n, hit in enumerate(hits, 1)],
            "message": "" if hits else "Nothing in the loaded manuals matches this."}


def with_recommendations(result: dict[str, Any], recs: dict[str, Any] | None) -> dict[str, Any]:
    """Add the verified recommendations to an investigate() result, when they are ready."""
    if not result.get("ok"):
        return result
    if recs is None:
        result["recommendations"] = None
        result["recommendations_note"] = ("Recommendations are still being prepared. Say the suggested checks "
                                          "now and say that the recommendations will appear on screen.")
        return result
    facts = {f["id"]: f["text"] for f in recs.get("facts", [])}
    result["recommendations"] = {
        "speak_rule": RECOMMEND_RULE,
        "kind": "ai_checked_against_the_data" if recs["status"] == "ai" else "rule_based",
        "dropped_drafts": recs.get("dropped", 0),
        "items": [{"action": r["action"], "why": r["why"], "based_on": [facts[i] for i in r["facts"] if i in facts],
                   "sources": [{"document": s["doc"], "page": s["page"], "quote": s["quote"]} for s in r["sources"]]}
                  for r in recs["recommendations"]],
    }
    return result
