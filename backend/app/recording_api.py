"""HTTP routes for recordings and incidents. Mounted by main.create_app.

Every route that changes state needs the cockpit token. Reads are open, like the
rest of the read-only API, so the incident view, the CSV and the report work from
any browser that can open the cockpit. Handlers that touch the engine are
coroutines, so they run on the event loop with the acquisition loop and never
race with it.
"""

from __future__ import annotations

from typing import Any, Callable
from urllib.parse import unquote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from . import incident_tools, ingest
from .incident_report import render_html
from .incidents import excerpt_csv, to_row
from .recommend import Recommender
from .recording_service import RecordingBusy, RecordingService


class AnalyseRequest(BaseModel):
    mapping: dict[str, Any]
    rate_hz: int | None = None


class IncidentRequest(BaseModel):
    incident_id: str | None = None


def add_routes(app: FastAPI, service: RecordingService, require_token: Callable[..., None],
               team: str, title: str, recommender: Recommender | None = None) -> None:
    engine = service.engine

    def find(incident_id: str) -> dict[str, Any]:
        incident = engine.incidents.get(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail=f"no incident {incident_id!r} in the loaded recording")
        return incident

    # -- recording -------------------------------------------------------------

    @app.post("/api/recording/inspect", dependencies=[Depends(require_token)])
    async def recording_inspect(request: Request) -> dict[str, Any]:
        raw = await request.body()
        name = unquote(request.headers.get("x-file-name", "")).strip()[:120] or "recording.csv"
        try:
            return service.inspect(raw, name)
        except ingest.IngestError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/recording/analyse", dependencies=[Depends(require_token)], status_code=202)
    async def recording_analyse(req: AnalyseRequest) -> dict[str, Any]:
        try:
            return service.start(req.mapping, req.rate_hz)
        except RecordingBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ingest.IngestError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/recording/status")
    async def recording_status() -> dict[str, Any]:
        return service.status()

    @app.post("/api/recording/restore", dependencies=[Depends(require_token)])
    async def recording_restore() -> dict[str, Any]:
        try:
            return service.restore()
        except RecordingBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    # -- incidents -------------------------------------------------------------

    @app.get("/api/incidents")
    async def incidents_list() -> dict[str, Any]:
        store = engine.incidents
        return {"incidents": store.rows(), "stats": store.stats, "source": store.source,
                "warnings": store.warnings, "recording": engine.recording_public()}

    @app.get("/api/incidents/{incident_id}")
    async def incident_detail(incident_id: str) -> dict[str, Any]:
        return find(incident_id)

    @app.post("/api/incidents/{incident_id}/acknowledge", dependencies=[Depends(require_token)])
    async def incident_acknowledge(incident_id: str) -> dict[str, Any]:
        find(incident_id)
        return to_row(engine.incidents.acknowledge(incident_id))

    @app.get("/api/incidents/{incident_id}/csv")
    async def incident_csv(incident_id: str) -> Response:
        return Response(excerpt_csv(find(incident_id)), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{incident_id}.csv"'})

    def prepared(incident: dict[str, Any]) -> dict[str, Any] | None:
        return recommender.cached(incident) if recommender else None

    @app.get("/api/incidents/{incident_id}/report")
    async def incident_report_html(incident_id: str) -> HTMLResponse:
        incident = find(incident_id)
        return HTMLResponse(render_html(incident, team=team, title_prefix=title, recommendations=prepared(incident)))

    @app.get("/report/incident/{incident_id}", include_in_schema=False)
    async def public_incident_report(incident_id: str) -> HTMLResponse:
        incident = engine.incidents.get(incident_id)
        if incident is None:
            return HTMLResponse(f"<h1>No incident</h1><p>{incident_id} is not in the loaded recording.</p>",
                                status_code=404)
        return HTMLResponse(render_html(incident, team=team, title_prefix=title, recommendations=prepared(incident)))

    # -- copilot tools ---------------------------------------------------------

    @app.post("/api/tools/list_incidents")
    async def tool_list_incidents() -> dict[str, Any]:
        return incident_tools.list_incidents(engine)

    @app.post("/api/tools/investigate_incident")
    async def tool_investigate_incident(req: IncidentRequest) -> dict[str, Any]:
        result = incident_tools.investigate(engine, req.incident_id)
        if recommender is None or not result.get("ok"):
            return result
        incident = engine.incidents.get(result["incident"]["id"])
        recs = await recommender.recommend_within(incident, 10.0) if incident else None
        return incident_tools.with_recommendations(result, recs)
