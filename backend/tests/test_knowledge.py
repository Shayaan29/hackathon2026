"""Manual search (RAG): chunking with citations, search, answers and the API."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.knowledge import KnowledgeBase, KnowledgeError, tokenize
from app.main import create_app
from app.realtime import instructions, tools

MANUAL = Path(__file__).resolve().parent.parent / "knowledge" / "optoNCDT-5500-manual-en.pdf"
needs_manual = pytest.mark.skipif(not MANUAL.exists(), reason="optoNCDT 5500 manual not in backend/knowledge")

GUIDE = """# Demo sensor guide

## Supply voltage

The sensor runs on 12 to 30 VDC. Power consumption is max. 5 W.

## Cleaning

Clean the optical window with a soft cloth and pure alcohol.

## Laser safety

The sensor uses a Class 2 laser.
"""


@pytest.fixture()
def guide_kb(tmp_path: Path) -> KnowledgeBase:
    (tmp_path / "guide.md").write_text(GUIDE, encoding="utf-8")
    return KnowledgeBase(tmp_path)


@pytest.fixture(scope="module")
def manual_kb(tmp_path_factory: pytest.TempPathFactory) -> KnowledgeBase:
    if not MANUAL.exists():
        pytest.skip("optoNCDT 5500 manual not in backend/knowledge")
    kb = KnowledgeBase(MANUAL.parent, cache_dir=tmp_path_factory.mktemp("cache"))
    kb.ensure()
    return kb


def test_tokenize_keeps_model_numbers_and_stems_words():
    tokens = tokenize("Linearity of the ILD5500-50 when operating")
    assert {"ild5500-50", "ild5500", "50", "linear", "operat"} <= set(tokens)
    assert tokenize("Operation") == tokenize("operating")
    assert "the" not in tokens


def test_markdown_sections_are_found_and_cited(guide_kb: KnowledgeBase):
    result = guide_kb.search("what supply voltage does it need")
    assert result["found"] is True
    top = result["hits"][0]
    assert top["section_title"] == "Supply voltage" and top["doc_title"] == "Demo sensor guide"
    assert "12 to 30 VDC" in top["text"]


def test_unrelated_question_is_not_found(guide_kb: KnowledgeBase):
    result = guide_kb.search("pizza recipe")
    assert result["found"] is False and result["hits"] == []
    answer = guide_kb.answer("pizza recipe")
    assert answer["mode"] == "none" and "not mentioned" in answer["answer"]


def test_offline_answer_quotes_the_manual_with_citation(guide_kb: KnowledgeBase):
    answer = guide_kb.answer("How do I clean the optical window?")
    assert answer["mode"] == "extractive"
    assert "pure alcohol" in answer["answer"] and "[Cleaning]" in answer["answer"]


def test_empty_question_is_refused(guide_kb: KnowledgeBase):
    with pytest.raises(KnowledgeError):
        guide_kb.search("   ")


def test_index_follows_the_folder(tmp_path: Path):
    kb = KnowledgeBase(tmp_path)
    assert kb.search("voltage")["found"] is False
    (tmp_path / "guide.md").write_text(GUIDE, encoding="utf-8")
    assert kb.search("voltage")["found"] is True
    assert kb.documents()["documents"][0]["name"] == "guide.md"


def test_document_filter(tmp_path: Path):
    (tmp_path / "guide.md").write_text(GUIDE, encoding="utf-8")
    (tmp_path / "other.md").write_text("# Other\n\n## Supply voltage\n\n24 VDC only.\n", encoding="utf-8")
    kb = KnowledgeBase(tmp_path)
    hits = kb.search("supply voltage", document="other")["hits"]
    assert hits and {h["doc"] for h in hits} == {"other.md"}


def _fake_openai(calls: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        body = json.loads(request.content)
        if request.url.path.endswith("/embeddings"):
            def vec(text: str) -> list[float]:
                text = text.lower()
                return [float("volt" in text or "power" in text), float("clean" in text), float("laser" in text), 0.1]
            return httpx.Response(200, json={"data": [{"index": i, "embedding": vec(t)}
                                                      for i, t in enumerate(body["input"])]})
        assert "Manual passages" in body["input"] and "ONLY" in body["instructions"]
        return httpx.Response(200, json={"output": [{"type": "message", "content": [
            {"type": "output_text", "text": "12 to 30 VDC [Supply voltage]"}]}]})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_hybrid_search_and_generated_answer(tmp_path: Path):
    (tmp_path / "guide.md").write_text(GUIDE, encoding="utf-8")
    calls: list[str] = []
    kb = KnowledgeBase(tmp_path, api_key="sk-test", http=_fake_openai(calls))
    # no shared word with the text, found by meaning through the embeddings
    result = kb.search("how much power does it draw")
    assert result["mode"] == "hybrid" and result["hits"][0]["section_title"] == "Supply voltage"
    answer = kb.answer("What voltage does it need?")
    assert answer["mode"] == "generated" and answer["answer"].startswith("12 to 30 VDC")
    assert "/v1/responses" in calls and "/v1/embeddings" in calls


def test_openai_failure_falls_back_to_keywords_and_quotes(tmp_path: Path):
    (tmp_path / "guide.md").write_text(GUIDE, encoding="utf-8")
    broken = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500, json={})))
    kb = KnowledgeBase(tmp_path, api_key="sk-test", http=broken)
    answer = kb.answer("supply voltage")
    assert answer["search"] == "keyword" and answer["mode"] == "extractive"
    assert "12 to 30 VDC" in answer["answer"]


# -- the real optoNCDT 5500 manual ---------------------------------------------------

@needs_manual
def test_manual_chunks_have_sections_and_printed_pages(manual_kb: KnowledgeBase):
    assert len(manual_kb.chunks) > 200
    sections = {c.section for c in manual_kb.chunks}
    assert {"3.3", "3.4", "5.3.2", "17.2.1.1"} <= sections
    tech = next(c for c in manual_kb.chunks if c.section == "3.3")
    assert tech.printed_page == 12 and tech.section_title == "Technical data ILD5500 General"
    # running header and footer are gone, the table of contents is not indexed
    assert all("optoNCDT 5500 | Page" not in c.text for c in manual_kb.chunks)
    assert not any("......" in c.text for c in manual_kb.chunks)


@needs_manual
@pytest.mark.parametrize("query, sections", [
    ("supply voltage", {"5.3.3", "3.3"}),
    ("linearity ILD5500-50", {"3.4"}),
    ("pin assignment", {"5.3.2"}),
    ("RS422 baud rate", {"8.1", "17.2.6.2"}),
    ("operating temperature range", {"1.5", "3.3"}),
    ("flashing red output LED", {"3.5"}),
])
def test_manual_questions_find_the_right_section(manual_kb: KnowledgeBase, query: str, sections: set[str]):
    hits = manual_kb.search(query, k=3)["hits"]
    assert sections & {h["section"] for h in hits}, [h["citation"] for h in hits]


@needs_manual
def test_spec_passages_carry_their_footnotes(manual_kb: KnowledgeBase):
    hit = next(h for h in manual_kb.search("measuring rate factory setting kHz", k=8)["hits"] if h["section"] == "3.3")
    assert any(n.startswith("[2] Factory setting: 4 kHz") for n in hit["footnotes"])
    answer = manual_kb.answer("What is the linearity of the ILD5500-50?")
    assert "Linearity [6]" in answer["answer"] and "[6] Value applies only to the standard measuring range" in answer["answer"]
    assert "Section 3.4 Technical data, measuring range, Page 12" in answer["answer"]


@needs_manual
def test_manual_cache_is_reused(manual_kb: KnowledgeBase):
    again = KnowledgeBase(manual_kb.folder, cache_dir=manual_kb.cache_dir)
    again.ensure()
    assert [c.text for c in again.chunks] == [c.text for c in manual_kb.chunks]


# -- API and the voice copilot ----------------------------------------------------------

def test_copilot_knows_the_manual_tool(settings):
    assert "search_manual" in [t["name"] for t in tools(settings)]
    assert "search_manual" in instructions(settings)


def test_manual_api(settings, tmp_path: Path):
    (tmp_path / "guide.md").write_text(GUIDE, encoding="utf-8")
    settings = dataclasses.replace(settings, knowledge_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        docs = client.get("/api/knowledge").json()
        assert docs["documents"][0]["title"] == "Demo sensor guide" and docs["search"] == "keyword"
        search = client.get("/api/knowledge/search", params={"q": "laser class"}).json()
        assert search["hits"][0]["section_title"] == "Laser safety"
        tool = client.post("/api/tools/search_manual", json={"query": "supply voltage"}).json()
        assert tool["found"] and tool["passages"][0]["source"] == "S1" and "how_to_answer" in tool
        missing = client.post("/api/tools/search_manual", json={"query": "pizza"}).json()
        assert missing["found"] is False and "Do not answer from memory" in missing["how_to_answer"]
        ask = client.post("/api/knowledge/ask", json={"query": "clean the window"}).json()
        assert ask["mode"] == "extractive" and "alcohol" in ask["answer"]
        assert client.get("/api/knowledge/file/guide.md").status_code == 200
        assert client.get("/api/knowledge/file/..%2Fsecrets.md").status_code == 404
        assert client.post("/api/knowledge/reload").status_code == 401
        assert client.post("/api/knowledge/reload", headers={"X-Cockpit-Token": "test-token"}).status_code == 200
