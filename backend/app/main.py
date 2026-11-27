"""FastAPI application: REST API, WebSocket streams and the built frontend."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

import httpx

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from . import __version__, assistant, explore, insights, report, timeline, tools, views
from .config import FRONTEND_DIST, Settings, load_settings
from .engine import DEMO_KINDS, Engine, EngineError
from .hub import COCKPIT, PHONE, Hub
from .knowledge import KnowledgeBase
from .knowledge_api import add_routes as add_knowledge_routes
from .openai_client import OpenAIClient
from .partcheck import PartCheckError
from .realtime import ALERT_PREFIX, RealtimeError, create_client_secret
from .recommend import Recommender
from .recording_api import add_routes as add_recording_routes
from .recording_service import RecordingService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("cockpit")

SHAKE_COOLDOWN_S = 6.0
SHAKE_GLOBAL_S = 2.5


class ToolRequest(BaseModel):
    sensor: str = "s1"
    window_s: float | None = None


class SettingRequest(BaseModel):
    sensor: str
    name: str
    value: Any
    confirmed: bool = False


class PartLearnRequest(BaseModel):
    kind: str = "part"                      # part (a good part) or empty (nothing under the sensor)
    add: bool = False                       # true: one more good part, the band grows to cover it
    margin_um: float | None = None          # allowed deviation around the taught parts, in µm


class PartMarginRequest(BaseModel):
    margin_um: float


class PartForgetRequest(BaseModel):
    what: str = "all"                       # all, part or empty


class ReportRequest(BaseModel):
    event_id: str | None = None


class DemoRequest(BaseModel):
    sensor: str | None = None
    amplitude: float | None = None
    frequency_hz: float | None = None
    decay_s: float | None = None
    rate_per_min: float | None = None
    factor: float | None = None
    duration_s: float | None = None
    share: float | None = None
    value: float | None = None

    def params(self) -> dict[str, float]:
        return {k: v for k, v in self.model_dump().items() if k != "sensor" and v is not None}


def create_app(settings: Settings | None = None,
               openai_factory: Callable[[], httpx.AsyncClient] | None = None) -> FastAPI:
    settings = settings or load_settings()
    hub = Hub()
    engine = Engine(settings, hub)
    openai = OpenAIClient(settings.openai_api_key, base_url=settings.openai_base_url, factory=openai_factory)
    knowledge = KnowledgeBase(settings.knowledge_dir, openai, settings.embed_model)
    recommender = Recommender(settings, knowledge, openai, engine)
    for warning in knowledge.warnings:
        log.warning("manual skipped: %s", warning)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await engine.start()
        if openai.enabled and knowledge.passages:
            warm = asyncio.ensure_future(knowledge.warm())
            app.state.warm = warm
        log.info("manuals: %d documents, %d passages", len(knowledge.documents), len(knowledge.passages))
        log.info("cockpit ready on http://localhost:%s (voice %s)", settings.port,
                 "on, model " + settings.realtime_model if settings.voice_enabled else "off")
        yield
        await engine.stop()

    app = FastAPI(title="KI Sensor Cockpit", version=__version__, lifespan=lifespan)
    app.state.engine = engine
    app.state.settings = settings
    app.state.knowledge = knowledge
    app.state.recommender = recommender
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["*"], allow_headers=["*"])

    def require_token(x_cockpit_token: str | None = Header(default=None)) -> None:
        if settings.cockpit_token and x_cockpit_token != settings.cockpit_token:
            raise HTTPException(status_code=401, detail="Missing or wrong cockpit token. "
                                "Open the cockpit once with ?token=<COCKPIT_TOKEN>.")

    def sensor_or_400(text: str | None) -> str:
        try:
            return engine.resolve_sensor(text)
        except EngineError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    def public_config() -> dict[str, Any]:
        return {
            "title": settings.cockpit.title,
            "team": settings.cockpit.team,
            "assistant_name": settings.cockpit.assistant_name,
            "language": settings.cockpit.language,
            "sensors": [s.public() for s in settings.sensors],
            "voice": {"enabled": settings.voice_enabled, "model": settings.realtime_model},
            "token_required": bool(settings.cockpit_token),
            "alert_prefix": ALERT_PREFIX,
            "demo_kinds": list(DEMO_KINDS),
            "part_rpm": settings.engine.part_rpm,
            "version": __version__,
        }

    # -- REST: read-only -----------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__, "uptime_s": round(time.time() - engine.started_at)}

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        return public_config()

    @app.get("/api/status")
    def status() -> dict[str, Any]:
        return engine.status_message()

    @app.get("/api/events")
    def events(limit: int = Query(50, ge=1, le=300)) -> dict[str, Any]:
        return {"events": engine.recent_events(limit)}

    @app.get("/api/history")
    def history(sensors: str = "s1", window_s: float = 300.0,
                max_points: int = Query(1200, ge=50, le=5000)) -> dict[str, Any]:
        ids = [sensor_or_400(s) for s in sensors.split(",") if s.strip()]
        return views.history(engine, ids or [sensor_or_400(None)], window_s, max_points)

    @app.get("/api/spectrum")
    def spectrum(sensor: str = "s1", window_s: float = 2.0, event_id: str | None = None,
                 max_hz: float = 100.0) -> dict[str, Any]:
        return views.spectrum(engine, sensor_or_400(sensor), window_s, event_id, max_hz)

    @app.get("/api/drift")
    def drift(sensor: str = "s1", window_s: float = 900.0, horizon_min: float = 60.0) -> dict[str, Any]:
        return views.drift(engine, sensor_or_400(sensor), window_s, horizon_min)

    @app.get("/api/explore")
    def explore_chart(chart: str, sensors: str = "s1", window_s: float | None = None,
                      group_by: str | None = None, metric: str | None = None) -> dict[str, Any]:
        ids = [sensor_or_400(s) for s in sensors.split(",") if s.strip()] or [sensor_or_400(None)]
        try:
            return explore.build(engine, chart, ids, window_s, group_by, metric)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/shift")
    def shift(sensor: str = "s1", points: int = Query(1800, ge=100, le=6000)) -> dict[str, Any]:
        return timeline.shift(engine, sensor_or_400(sensor), points)

    @app.get("/api/range")
    def time_range(sensor: str, t0: float, t1: float,
                   points: int = Query(1600, ge=100, le=6000)) -> dict[str, Any]:
        return timeline.range_data(engine, sensor_or_400(sensor), t0, t1, points)

    @app.get("/api/insights")
    def chart_insights(view: str, sensors: str = "s1", window_s: float | None = None,
                       event_id: str | None = None) -> dict[str, Any]:
        """The steps for explaining one of the live chart views."""
        ids = [sensor_or_400(s) for s in sensors.split(",") if s.strip()] or [sensor_or_400(None)]
        if view in ("timeseries", "compare"):
            data = views.history(engine, ids if view == "compare" else ids[:1],
                                 window_s or (60.0 if view == "compare" else 300.0))
            steps = insights.for_history(engine, data)
        elif view == "spectrum":
            steps = insights.for_spectrum(engine, views.spectrum(engine, ids[0], 2.0, event_id))
        elif view == "drift":
            steps = insights.for_drift(engine, views.drift(engine, ids[0], window_s or 900.0))
        else:
            raise HTTPException(status_code=400, detail=f"no explanation for view {view!r}")
        return {"view": view, "insights": steps}

    # -- REST: AI tools ------------------------------------------------------

    @app.post("/api/tools/get_data")
    def tool_get_data(req: ToolRequest) -> dict[str, Any]:
        try:
            return tools.get_data(engine, req.sensor, req.window_s or 10.0)
        except EngineError as exc:
            return {"ok": False, "error": str(exc)}

    @app.post("/api/tools/analyze")
    def tool_analyze(req: ToolRequest) -> dict[str, Any]:
        try:
            return tools.analyze(engine, req.sensor, req.window_s or 60.0)
        except EngineError as exc:
            return {"ok": False, "error": str(exc)}

    @app.post("/api/tools/change_setting", dependencies=[Depends(require_token)])
    def tool_change_setting(req: SettingRequest) -> dict[str, Any]:
        return tools.change_setting(engine, req.sensor, req.name, req.value, req.confirmed)

    @app.post("/api/tools/suggest_settings")
    def tool_suggest_settings(req: ToolRequest) -> dict[str, Any]:
        try:
            plan = assistant.suggest(engine, req.sensor)
        except EngineError as exc:
            return {"ok": False, "error": str(exc)}
        plan.pop("preview", None)
        return plan

    @app.post("/api/tools/create_report")
    def tool_create_report(req: ReportRequest) -> dict[str, Any]:
        try:
            rep = report.build(engine, req.event_id)
        except report.ReportError as exc:
            return {"ok": False, "error": str(exc)}
        hub.broadcast({"type": "report", "id": rep["id"], "title": rep["title"]}, kinds=(COCKPIT,))
        return {"ok": True, "id": rep["id"], "title": rep["title"], "url": f"/report/{rep['id']}",
                "likely_causes": rep["causes"], "next_steps": rep["actions"],
                "setup_check": rep["setup"]["summary"]}

    # -- REST: setup assistant and incident reports -----------------------------

    @app.get("/api/assistant/{sensor}")
    def assistant_suggest(sensor: str) -> dict[str, Any]:
        try:
            return assistant.suggest(engine, sensor)
        except EngineError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/assistant/{sensor}/apply", dependencies=[Depends(require_token)])
    def assistant_apply(sensor: str) -> dict[str, Any]:
        try:
            return assistant.apply(engine, sensor)
        except EngineError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # -- REST: part check (teach one part, flag every other) ---------------------

    @app.get("/api/part/{sensor}")
    def part_state(sensor: str) -> dict[str, Any]:
        return engine.partcheck.public(sensor_or_400(sensor))

    @app.post("/api/part/{sensor}/learn", dependencies=[Depends(require_token)])
    def part_learn(sensor: str, req: PartLearnRequest) -> dict[str, Any]:
        sid = sensor_or_400(sensor)
        try:
            return engine.partcheck.learn(sid, engine.buffers[sid], req.kind, req.add, req.margin_um)
        except PartCheckError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/part/{sensor}/margin", dependencies=[Depends(require_token)])
    def part_margin(sensor: str, req: PartMarginRequest) -> dict[str, Any]:
        try:
            return engine.partcheck.set_margin(sensor_or_400(sensor), req.margin_um)
        except PartCheckError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/part/{sensor}/forget", dependencies=[Depends(require_token)])
    def part_forget(sensor: str, req: PartForgetRequest) -> dict[str, Any]:
        return engine.partcheck.forget(sensor_or_400(sensor), req.what)

    @app.get("/api/reports/{event_id}")
    def report_json(event_id: str) -> dict[str, Any]:
        try:
            return report.build(engine, event_id)
        except report.ReportError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/report/{event_id}", include_in_schema=False)
    def report_page(event_id: str, embed: bool = False) -> HTMLResponse:
        try:
            return HTMLResponse(report.render_html(report.build(engine, event_id), embed=embed))
        except report.ReportError as exc:
            return HTMLResponse(f"<h1>No report</h1><p>{exc}</p>", status_code=404)

    # -- REST: voice and demo controls ---------------------------------------

    @app.post("/api/realtime/session", dependencies=[Depends(require_token)])
    async def realtime_session() -> dict[str, Any]:
        try:
            return await create_client_secret(settings)
        except RealtimeError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @app.post("/api/demo/{kind}", dependencies=[Depends(require_token)])
    def demo(kind: str, req: DemoRequest | None = None) -> dict[str, Any]:
        req = req or DemoRequest()
        try:
            return engine.inject(kind, req.sensor, **req.params())
        except EngineError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/auth/check", dependencies=[Depends(require_token)])
    def auth_check() -> dict[str, bool]:
        return {"ok": True}

    # -- recordings and incidents (the investigation chain) -----------------------

    recording = RecordingService(settings, engine)
    recording.after_analysis = recommender.prefetch
    app.state.recording = recording
    add_recording_routes(app, recording, require_token, settings.cockpit.team, settings.cockpit.title,
                         recommender=recommender)
    add_knowledge_routes(app, knowledge, recommender, engine, require_token)

    # -- WebSockets ----------------------------------------------------------

    shake_state = {"last": 0.0}

    def handle_phone_message(client: Any, raw: str) -> None:
        """A judge shook their phone: knock the fixture, let the detector find it."""
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if msg.get("type") != "shake":
            return
        now = time.time()
        last_own = getattr(client, "last_shake", 0.0)
        if now - last_own < SHAKE_COOLDOWN_S or now - shake_state["last"] < SHAKE_GLOBAL_S:
            hub.send(client, {"type": "shake_ack", "ok": False, "reason": "busy",
                              "retry_s": round(max(SHAKE_COOLDOWN_S - (now - last_own),
                                                   SHAKE_GLOBAL_S - (now - shake_state["last"])), 1)})
            return
        name = " ".join(str(msg.get("name") or "").split())[:24] or "A judge"
        try:
            intensity = min(max(float(msg.get("intensity", 1.0)), 0.0), 3.0)
        except (TypeError, ValueError):
            intensity = 1.0
        target = settings.sensors[0].id
        try:
            engine.inject("tap", target, amplitude=12.0 + 8.0 * intensity)
        except EngineError as exc:
            hub.send(client, {"type": "shake_ack", "ok": False, "reason": "unsupported", "detail": str(exc)})
            return
        client.last_shake = now
        shake_state["last"] = now
        log.info("shake from %s (intensity %.1f)", name, intensity)
        hub.send(client, {"type": "shake_ack", "ok": True})
        hub.broadcast({"type": "shake", "name": name, "sensor": target, "intensity": round(intensity, 2),
                       "t": now}, kinds=(COCKPIT, PHONE))

    async def serve_socket(ws: WebSocket, kind: str, hello: dict[str, Any]) -> None:
        await ws.accept()
        client = hub.add(ws, kind)
        hub.send(client, hello)
        pump = asyncio.create_task(hub.pump(client))
        try:
            while True:
                message = await ws.receive_text()
                if message == "ping":
                    hub.send(client, {"type": "pong", "t": time.time()})
                elif kind == PHONE and message.startswith("{"):
                    handle_phone_message(client, message)
        except WebSocketDisconnect:
            pass
        finally:
            pump.cancel()
            hub.remove(client)

    @app.websocket("/ws/cockpit")
    async def ws_cockpit(ws: WebSocket) -> None:
        await serve_socket(ws, COCKPIT, {"type": "hello", "config": public_config(),
                                         "events": engine.recent_events(30),
                                         "status": engine.status_message()})

    @app.websocket("/ws/phone")
    async def ws_phone(ws: WebSocket) -> None:
        await serve_socket(ws, PHONE, {
            "type": "hello",
            "title": settings.cockpit.title,
            "team": settings.cockpit.team,
            "sensors": [{"id": s.id, "name": s.name} for s in settings.sensors],
            "events": engine.recent_events(5),
        })

    # -- Frontend ------------------------------------------------------------

    def frontend_file(name: str) -> FileResponse | HTMLResponse:
        path = FRONTEND_DIST / name
        if path.exists():
            return FileResponse(path)
        return HTMLResponse(
            "<h1>Frontend not built</h1><p>Run <code>npm install</code> and <code>npm run build</code> "
            "in the <code>frontend</code> folder, or use the dev server at "
            "<a href='http://localhost:5173'>http://localhost:5173</a>.</p>", status_code=503)

    @app.get("/", include_in_schema=False)
    def index() -> Any:
        return frontend_file("index.html")

    @app.get("/phone", include_in_schema=False)
    def phone() -> Any:
        return frontend_file("phone.html")

    @app.get("/{path:path}", include_in_schema=False)
    def static_files(path: str) -> Any:
        if path.startswith(("api/", "ws/")):
            raise HTTPException(status_code=404)
        candidate = (FRONTEND_DIST / path).resolve()
        if FRONTEND_DIST.exists() and candidate.is_file() and FRONTEND_DIST.resolve() in candidate.parents:
            return FileResponse(candidate)
        # "/phone/" or a doubled "/phone/phone" (QR scanners, pasted links) must
        # still open the phone page, never the full cockpit
        if path.rstrip("/").split("/")[0] == "phone":
            return frontend_file("phone.html")
        return frontend_file("index.html")

    return app


app = create_app()
