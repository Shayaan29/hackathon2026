"""HTTP routes for the manuals and the recommendations. Mounted by main.create_app.

Reading what is loaded and what was already prepared is open, like the rest of the
read-only API. Everything that writes a file or spends API credit needs the cockpit
token.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable
from urllib.parse import quote, unquote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from . import incident_tools
from .engine import Engine
from .knowledge import KnowledgeBase, KnowledgeError, clean_name, parse_document
from .recommend import Recommender


class SearchRequest(BaseModel):
    query: str
    k: int = 5


class RecommendRequest(BaseModel):
    force: bool = False


def add_routes(app: FastAPI, kb: KnowledgeBase, recommender: Recommender, engine: Engine,
               require_token: Callable[..., None]) -> None:
    background: set[asyncio.Future[Any]] = set()

    def find(incident_id: str) -> dict[str, Any]:
        incident = engine.incidents.get(incident_id)
        if incident is None:
            raise HTTPException(status_code=404, detail=f"no incident {incident_id!r} in the loaded recording")
        return incident

    def full_status() -> dict[str, Any]:
        return {**kb.status(), **recommender.status()}

    # -- manuals -------------------------------------------------------------------

    @app.get("/api/knowledge")
    async def knowledge_status() -> dict[str, Any]:
        return full_status()

    @app.post("/api/knowledge/upload", dependencies=[Depends(require_token)])
    async def knowledge_upload(request: Request) -> dict[str, Any]:
        raw = await request.body()
        name = unquote(request.headers.get("x-file-name", "")).strip() or "manual.pdf"
        try:
            parsed = await asyncio.to_thread(parse_document, name, raw)
        except KnowledgeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        document = kb.add_parsed(parsed, raw)
        task = asyncio.ensure_future(kb.warm())
        background.add(task)
        task.add_done_callback(background.discard)
        return {"document": document, **full_status()}

    @app.get("/api/knowledge/file/{name}")
    async def knowledge_file(name: str) -> FileResponse:
        """The manual itself, so a quote can link to its page (PDF viewers open `#page=N`)."""
        try:
            clean = clean_name(name)
        except KnowledgeError as exc:
            raise HTTPException(status_code=404, detail="no such manual") from exc
        document = kb.documents.get(clean)
        if document is None:
            raise HTTPException(status_code=404, detail=f"no manual called {clean!r}")
        media = {"pdf": "application/pdf", "markdown": "text/markdown; charset=utf-8",
                 "text": "text/plain; charset=utf-8"}[document.kind]
        return FileResponse(kb.folder / clean, media_type=media,
                            headers={"Content-Disposition": f"inline; filename*=UTF-8''{quote(clean)}"})

    @app.delete("/api/knowledge/{name}", dependencies=[Depends(require_token)])
    async def knowledge_delete(name: str) -> dict[str, Any]:
        try:
            found = kb.remove(name)
        except KnowledgeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if not found:
            raise HTTPException(status_code=404, detail=f"no manual called {name!r}")
        return full_status()

    @app.post("/api/knowledge/search", dependencies=[Depends(require_token)])
    async def knowledge_search(req: SearchRequest) -> dict[str, Any]:
        hits = await kb.search(req.query, k=req.k)
        return {"hits": [h.passage.public(h.score) | {"via": h.via} for h in hits]}

    # -- recommendations -------------------------------------------------------------

    @app.get("/api/incidents/{incident_id}/recommendations")
    async def recommendations_read(incident_id: str) -> dict[str, Any]:
        """What was already prepared. Never starts a model call."""
        result = recommender.cached(find(incident_id))
        return result or {"status": "none", "incident": incident_id, "ai": recommender.client.enabled}

    @app.post("/api/incidents/{incident_id}/recommendations", dependencies=[Depends(require_token)])
    async def recommendations_make(incident_id: str, req: RecommendRequest | None = None) -> dict[str, Any]:
        return await recommender.recommend(find(incident_id), force=bool(req and req.force))

    # -- copilot tool -------------------------------------------------------------------

    @app.post("/api/tools/search_manual", dependencies=[Depends(require_token)])
    async def tool_search_manual(req: SearchRequest) -> dict[str, Any]:
        return await incident_tools.search_manual(kb, req.query, limit=max(1, min(req.k, 6)))
