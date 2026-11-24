"""Load a recording into the cockpit: inspect the file, analyse it, then play it in
place of the simulator.

Only one file is held at a time and only one analysis runs at a time. The heavy
work runs in a worker thread, so the live acquisition loop keeps running while a
file is analysed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from . import ingest
from .config import ConfigError, Settings
from .engine import Engine
from .incidents import analyse
from .ingest import IngestError, Mapping
from .investigate import load_playbook

log = logging.getLogger("cockpit.recording")


class RecordingBusy(RuntimeError):
    """An analysis is already running."""


@dataclass
class Job:
    state: str = "idle"          # idle | running | done | error
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    result: dict[str, Any] | None = None


class RecordingService:
    def __init__(self, settings: Settings, engine: Engine) -> None:
        self.settings = settings
        self.engine = engine
        self.pending: dict[str, Any] | None = None
        self.job = Job()
        self._task: asyncio.Task[None] | None = None
        self.playbook: dict[str, list[dict[str, Any]]] = {}
        self.playbook_error: str | None = None
        try:
            self.playbook = load_playbook()
        except ConfigError as exc:
            self.playbook_error = str(exc)
            log.error("suggested checks not loaded: %s", exc)

    # -- steps the browser calls ---------------------------------------------

    def inspect(self, raw: bytes, name: str) -> dict[str, Any]:
        result = ingest.inspect(raw, default_rpm=0.0)
        digest = hashlib.sha256(raw).hexdigest()[:12]
        self.pending = {"raw": raw, "name": name, "digest": digest}
        return {**result, "file": {"name": name, "digest": digest, "bytes": len(raw)}}

    def start(self, mapping_data: dict[str, Any], rate_hz: int | None) -> dict[str, Any]:
        if self.job.state == "running":
            raise RecordingBusy("An analysis is already running. Wait for it to finish.")
        if self.pending is None:
            raise IngestError("No file is loaded. Upload a recording first.")
        mapping = Mapping.from_dict(mapping_data)
        rate = int(rate_hz or self.settings.engine.analysis_rate_hz)
        if not 50 <= rate <= 5000:
            raise IngestError("The analysis rate must be between 50 and 5000 Hz.")
        self.job = Job(state="running", progress=0.0, message="Starting")
        self._task = asyncio.create_task(self._run(mapping, dict(self.pending), rate, dict(mapping_data)))
        return self.status()

    def restore(self) -> dict[str, Any]:
        if self.job.state == "running":
            raise RecordingBusy("An analysis is running. Wait for it to finish first.")
        self.engine.restore_simulator()
        self.job = Job()
        return self.status()

    def status(self) -> dict[str, Any]:
        job = self.job
        pending = None
        if self.pending is not None:
            pending = {"name": self.pending["name"], "digest": self.pending["digest"]}
        return {"state": job.state, "progress": round(job.progress, 3), "message": job.message,
                "error": job.error, "result": job.result, "pending": pending,
                "recording": self.engine.recording_public(), "playbook_error": self.playbook_error,
                "playbook_checks": sum(len(items) for items in self.playbook.values())}

    # -- the background job ------------------------------------------------------

    async def _run(self, mapping: Mapping, pending: dict[str, Any], rate: int,
                   mapping_data: dict[str, Any]) -> None:
        job = self.job

        def progress(fraction: float, message: str) -> None:
            job.progress = max(0.0, min(1.0, float(fraction)))
            job.message = message

        try:
            text, _ = ingest.decode(pending["raw"])
            recording = await asyncio.to_thread(ingest.load, text, mapping, rate, pending["name"], progress)
            digest = hashlib.sha256((pending["digest"] + json.dumps(mapping_data, sort_keys=True)).encode())
            source = {"file": pending["name"], "digest": digest.hexdigest()[:12], "rows": recording.rows,
                      "duration_s": recording.duration_s, "source_rate_hz": recording.source_rate_hz,
                      "rate_hz": rate, "part_rpm": mapping.part_rpm or None,
                      "metadata": recording.metadata,
                      "analysed_at": time.strftime("%Y-%m-%d %H:%M:%S")}
            analysis = await asyncio.to_thread(analyse, recording, self.settings, playbook=self.playbook,
                                               part_rpm=mapping.part_rpm, source=source, progress=progress)
            self.engine.incidents.replace(analysis, source)
            self.engine.adopt_recording(recording, source)
            job.state, job.progress, job.message = "done", 1.0, "Playing the recording"
            job.result = {"stats": analysis.stats(), "warnings": recording.warnings + analysis.warnings,
                          "source": source}
            log.info("recording %s: %d incidents", pending["name"], analysis.total)
        except IngestError as exc:
            job.state, job.error, job.message = "error", str(exc), "Stopped"
        except Exception as exc:  # noqa: BLE001 - shown to the user, the cockpit keeps running
            log.exception("recording analysis failed")
            job.state, job.error, job.message = "error", f"Unexpected error: {exc}", "Stopped"
