"""Written answers from the manuals (manual_rag) and the typed chat, offline or with a fake OpenAI."""

from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.manual_rag import KnowledgeBase
from app.openai_client import OpenAIClient

MANUAL = """# Operating Instructions Test Sensor

## Proper environment

Protection class: IP67
Temperature range operation: 0 ... +50 °C
Temperature range storage: -20 ... +70 °C

## Control and indicator elements

State LED red: No distance value available, target outside the measuring range.
State LED green: Target within the measuring range.
"""

TOKEN = {"X-Cockpit-Token": "test-token"}


@pytest.fixture()
def manual_dir(tmp_path):
    folder = tmp_path / "manuals"
    folder.mkdir()
    (folder / "test-manual.md").write_text(MANUAL, encoding="utf-8")
    (folder / "README.md").write_text("# Manuals the AI reads\n\nPut manuals here.", encoding="utf-8")
    return folder


def test_search_finds_section_with_citation(manual_dir):
    result = KnowledgeBase(manual_dir).search("what does the red LED mean")
    assert result["found"] and result["mode"] == "keyword"
    top = result["hits"][0]
    assert top["section_title"] == "Control and indicator elements" and "red" in top["text"]


def test_folder_note_is_not_a_manual(manual_dir):
    assert [p.name for p in KnowledgeBase(manual_dir).files()] == ["test-manual.md"]


def test_answer_without_key_quotes_the_manual(manual_dir):
    answer = KnowledgeBase(manual_dir).answer("operating temperature range")
    assert answer["found"] and answer["mode"] == "extractive" and "+50" in answer["answer"]


def test_unknown_question_is_not_answered(manual_dir):
    answer = KnowledgeBase(manual_dir).answer("zzqx flux capacitor")
    assert not answer["found"] and answer["mode"] == "none"


def test_answer_with_key_is_written_by_the_model(manual_dir):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(request.url.path)
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"index": i, "embedding": [1.0, float(i)]}
                                                      for i in range(len(body["input"]))]})
        assert "Manual passages" in body["input"] and body["reasoning"] == {"effort": "low"}
        return httpx.Response(200, json={"output": [{"type": "message", "content": [
            {"type": "output_text", "text": "IP67 [Section Proper environment]"}]}]})

    client = OpenAIClient("sk-test", factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    answer = KnowledgeBase(manual_dir, client).answer("protection class")
    assert answer["mode"] == "generated" and answer["answer"].startswith("IP67")
    assert any(p.endswith("/responses") for p in seen)


def test_routes_in_the_cockpit(settings, manual_dir):
    app = create_app(replace(settings, knowledge_dir=manual_dir))
    with TestClient(app) as client:
        ask = client.post("/api/knowledge/ask", json={"question": "protection class"}, headers=TOKEN).json()
        assert ask["found"] and "IP67" in ask["answer"] and ask["sources"][0]["citation"]
        tool = client.post("/api/tools/search_manual", json={"query": "green LED"}, headers=TOKEN).json()
        first = tool["passages"][0]
        assert tool["ok"] and tool["found"] and first["doc"] == "test-manual.md" and first["uid"] and first["cite"]
        assert "speak_rule" in tool
        assert client.post("/api/knowledge/ask", json={"question": "x"}).status_code == 401
        assert client.post("/api/knowledge/ask", json={"question": ""}, headers=TOKEN).status_code == 422


def test_chat_turn_returns_text_and_tool_calls(settings, manual_dir):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if request.url.path.endswith("/embeddings"):  # the manuals warm up in the background
            inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
            return httpx.Response(200, json={"data": [{"index": i, "embedding": [1.0, 0.0]}
                                                      for i in range(len(inputs))]})
        seen["body"] = body
        return httpx.Response(200, json={"id": "resp_1", "output": [
            {"type": "function_call", "call_id": "c1", "name": "search_manual", "arguments": '{"query":"red LED"}'},
            {"type": "message", "content": [{"type": "output_text", "text": "Checking the manual."}]},
        ]})

    app = create_app(replace(settings, knowledge_dir=manual_dir, openai_api_key="sk-test"),
                     openai_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with TestClient(app) as client:
        turn = client.post("/api/chat", headers=TOKEN, json={
            "items": [{"role": "user", "content": "red LED?"}], "previous_response_id": "resp_0"}).json()
    assert turn == {"id": "resp_1", "text": "Checking the manual.",
                    "calls": [{"call_id": "c1", "name": "search_manual", "arguments": '{"query":"red LED"}'}]}
    body = seen["body"]
    assert body["previous_response_id"] == "resp_0" and "Text mode" in body["instructions"]
    assert "search_manual" in {t["name"] for t in body["tools"]}


def test_chat_without_key_is_refused(settings):
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/chat", headers=TOKEN, json={"items": [{"role": "user", "content": "hi"}]})
        assert response.status_code == 503
