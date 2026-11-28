"""Checks the manuals Epsilon answers from, on this computer, with your real OpenAI key.

Put the key into backend/.env and the manuals (PDF, text, Markdown) into backend/data/knowledge/.
Then, in the backend folder:

    .venv/bin/python scripts/rag_check.py             (Windows: .venv\\Scripts\\python scripts\\rag_check.py)

It does not need the cockpit to be running. It prints PASS, WARN or FAIL for each step and
exits with 0 when nothing failed. The cost is a fraction of a cent: a handful of embedding
calls. The key is never printed.

What it checks, in order:
  1. a key is set and OpenAI accepts it
  2. the manuals are found and readable (documents, pages, passages)
  3. search by meaning works (WARN if not: keyword search still works)
  4. typical questions about the sensor find a passage in the manuals
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Callable

import httpx

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND / "scripts"))

from app.config import Settings, load_settings  # noqa: E402
from app.knowledge import KnowledgeBase  # noqa: E402
from app.openai_client import OpenAIClient, OpenAIError  # noqa: E402

# questions an operator or the jury asks about an optoNCDT sensor
QUESTIONS = {
    "warm-up": "How long does the sensor need to warm up before it measures reproducibly?",
    "measuring range": "What is the measuring range and where does it start?",
    "invalid readings": "Why does the sensor report no peak or invalid readings, and what can I check?",
    "mounting": "How should the sensor be mounted relative to the target?",
}


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

    # 4. typical questions find something
    for topic, question in QUESTIONS.items():
        hits = await kb.search(question, k=3)
        if hits:
            top = hits[0].passage
            where = f"{top.doc}" + (f", page {top.page}" if top.page else "")
            report.passed(f"{topic}: best passage is in {where}.")
            report.info(top.text[:110].replace("\n", " ") + "...")
        else:
            report.warn(f"{topic}: nothing in the manuals matches. Epsilon will say the manuals do not cover it.")
    return report.failed


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    failed = asyncio.run(run(load_settings()))
    print("\nEverything passed." if failed == 0 else f"\n{failed} step(s) failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
