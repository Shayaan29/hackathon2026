"""Checks the manuals and the AI recommendations on this computer, with your real OpenAI key.

Put the key into backend/.env and the manuals (PDF, text, Markdown) into backend/data/knowledge/.
Then, in the backend folder:

    .venv/bin/python scripts/rag_check.py             (Windows: .venv\\Scripts\\python scripts\\rag_check.py)

It does not need the cockpit to be running. It prints PASS, WARN or FAIL for each step and
exits with 0 when nothing failed. The cost is a few cents: one model call and a handful of
embedding calls. The key is never printed.

What it checks, in order:
  1. a key is set and OpenAI accepts it, and which text model will be used
  2. the manuals are found and readable (documents, pages, passages)
  3. search by meaning works (WARN if not: keyword search still works)
  4. each kind of incident finds a passage in the manuals
  5. the demo recording's first incident gets recommendations, and how many drafts the checker dropped
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Callable

import httpx

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from app import ingest  # noqa: E402
from app.config import Settings, load_settings  # noqa: E402
from app.incidents import analyse  # noqa: E402
from app.investigate import load_playbook  # noqa: E402
from app.knowledge import KnowledgeBase  # noqa: E402
from app.openai_client import OpenAIClient, OpenAIError  # noqa: E402
from app.recommend import KIND_TERMS, KIND_WORDS, Recommender, choose_model  # noqa: E402

DEMO_RPM = 8.6


class Report:
    def __init__(self, out: Callable[[str], None]) -> None:
        self.out = out
        self.failed = 0

    def line(self, tag: str, text: str) -> None:
        self.out(f"{tag:<5} {text}")

    def passed(self, text: str) -> None:
        self.line("PASS", text)

    def warn(self, text: str) -> None:
        self.line("WARN", text)

    def info(self, text: str) -> None:
        self.line("", "      " + text)

    def fail(self, text: str) -> None:
        self.failed += 1
        self.line("FAIL", text)


def first_demo_incident(settings: Settings) -> dict[str, Any]:
    from make_demo_recording import make_text  # the same file the cockpit demo uses

    text = make_text("german")
    info = ingest.inspect(text.encode("utf-8"), default_rpm=0.0)
    mapping = ingest.Mapping.from_dict(info["mapping"])
    recording = ingest.load(text, mapping, 500, "demo_recording.csv", lambda fraction, message: None)
    source = {"file": "demo_recording.csv", "digest": "rag-check", "rows": recording.rows,
              "duration_s": recording.duration_s, "source_rate_hz": recording.source_rate_hz, "rate_hz": 500,
              "part_rpm": DEMO_RPM, "metadata": recording.metadata, "analysed_at": ""}
    analysis = analyse(recording, settings, playbook=load_playbook(), part_rpm=DEMO_RPM, source=source,
                       progress=lambda fraction, message: None)
    return analysis.incidents[0]


async def run(settings: Settings, factory: Callable[[], httpx.AsyncClient] | None = None,
              out: Callable[[str], None] = print) -> int:
    report = Report(out)
    client = OpenAIClient(settings.openai_api_key, base_url=settings.openai_base_url, factory=factory)

    # 1. the key and the model
    if not client.enabled:
        report.fail("No OPENAI_API_KEY. Put it into backend/.env (copy .env.example) and run this again.")
        return report.failed
    try:
        data = await client.request("GET", "/models", timeout=30.0)
        ids = [m["id"] for m in data.get("data", []) if isinstance(m, dict) and isinstance(m.get("id"), str)]
        report.passed(f"OpenAI accepted the key ({len(ids)} models visible).")
    except OpenAIError as exc:
        report.fail(str(exc))
        return report.failed
    chosen = settings.text_model or choose_model(ids)
    if chosen and (settings.text_model or chosen in ids):
        report.passed(f"Text model for recommendations: {chosen}"
                      + (" (from OPENAI_TEXT_MODEL)." if settings.text_model else " (chosen automatically)."))
    else:
        report.warn("No gpt-* text model found in the list. The cockpit will try gpt-4.1-mini. "
                    "Set OPENAI_TEXT_MODEL in backend/.env to a model your key can use.")

    # 2. the manuals
    kb = KnowledgeBase(settings.knowledge_dir, client, settings.embed_model)
    for warning in kb.warnings:
        report.warn(f"Manual not loaded: {warning}")
    if not kb.documents:
        report.fail(f"No manual found in {settings.knowledge_dir}. Copy the PDFs of your sensors there "
                    "(or add them in the cockpit under Manuals) and run this again.")
        return report.failed
    for doc in kb.documents.values():
        pages = f"{doc.pages} pages, " if doc.pages else ""
        report.passed(f"Read {doc.name}: {pages}{len(doc.passages)} passages.")

    # 3. search by meaning
    await kb._embeddings_ready()  # noqa: SLF001 - the check is about exactly this step
    status = kb.status()["embeddings"]
    if status["error"]:
        report.warn(f"Search by meaning is not available: {status['error']} Keyword search still works.")
    else:
        report.passed(f"Search by meaning works ({status['model']}, {status['ready']} passages indexed).")

    # 4. every kind of incident finds something
    for kind, terms in KIND_TERMS.items():
        hits = await kb.search(f"{KIND_WORDS[kind]} on a displacement sensor, what to check: {terms}", k=3)
        if hits:
            top = hits[0].passage
            where = f"{top.doc}" + (f", page {top.page}" if top.page else "")
            report.passed(f"{kind}: best passage is in {where}.")
            report.info(top.text[:110].replace("\n", " ") + "...")
        else:
            report.warn(f"{kind}: nothing in the manuals matches. The AI will then work from the measurements alone.")

    # 5. the recommendations for a real incident
    incident = first_demo_incident(settings)
    recommender = Recommender(settings, kb, client, engine=None)
    result = await recommender.recommend(incident, force=True)
    if result["status"] != "ai":
        report.fail(f"No AI recommendations: {result['note']}")
        return report.failed
    report.passed(f"{incident['id']} ({incident['title']}): {len(result['recommendations'])} recommendations "
                  f"by {result['model']} in {result['seconds']} s, {result['dropped']} draft(s) dropped by the checker.")
    for rec in result["recommendations"]:
        report.info(f"{rec['id']} [{rec['basis']}] {rec['action']}")
        for source in rec["sources"]:
            where = source["doc"] + (f", page {source['page']}" if source["page"] else "")
            report.info(f"     quote from {where}: \"{source['quote']}\"")
    if not any(rec["sources"] for rec in result["recommendations"]):
        report.warn("None of the recommendations quotes a manual. Check that the manuals fit the sensor kind, "
                    "or that they contain selectable text.")
    return report.failed


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    failed = asyncio.run(run(load_settings()))
    print("\nEverything passed." if failed == 0 else f"\n{failed} step(s) failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
