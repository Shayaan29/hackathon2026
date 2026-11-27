"""Manuals, search, the verifier and the recommendations over HTTP, against a fake OpenAI."""

from __future__ import annotations

import dataclasses
import json
import re
import sys
import textwrap
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND / "scripts"))

from make_demo_recording import make_text  # noqa: E402

from app.config import load_settings  # noqa: E402
from app.knowledge import (KnowledgeBase, KnowledgeError, clean_name, is_contents_page, parse_document,  # noqa: E402
                           split_text, tokenize)
from app.main import create_app  # noqa: E402
from app.openai_client import OpenAIClient, OpenAIError, redact  # noqa: E402
from app.recommend import (Fact, build_facts, build_queries, build_query, choose_model, quote_in, verify)  # noqa: E402
from app.knowledge import Passage  # noqa: E402

TOKEN = {"X-Cockpit-Token": "test-token"}
KEY = "sk-test-0123456789abcdef"

PAGE_ONE = ("Mounting and vibration. Mount the sensor on a rigid bracket. Vibration of the sensor or of the target "
            "shows up as an oscillating measurement value, so clamp both firmly and keep the cable away from "
            "moving parts. Check the clamp after every tool change.")
PAGE_TWO = ("Thermal drift. Allow a warm-up time of 20 minutes before you measure. Temperature changes of the "
            "sensor housing cause a slow drift of the measured value, so keep the housing out of draughts.")


def make_pdf(pages: list[str]) -> bytes:
    """A small but valid PDF with one text block per page, so the PDF path is tested without extra packages."""
    count = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(count))
    body = [b"<< /Type /Catalog /Pages 2 0 R >>", f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode(),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for i, text in enumerate(pages):
        lines = textwrap.wrap(text, 70)
        escaped = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
        stream = ("BT /F1 11 Tf 72 740 Td 14 TL\n" + "\n".join(f"({line}) Tj T*" for line in escaped) + "\nET").encode()
        body.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
                    f"/Contents {5 + 2 * i} 0 R >>".encode())
        body.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, item in enumerate(body, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + item + b"\nendobj\n"
    start = len(out)
    out += f"xref\n0 {len(body) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(body) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    return bytes(out)


# -- documents and search ---------------------------------------------------------------


def test_clean_name_only_allows_manual_files():
    assert clean_name("C:\\Users\\me\\man--optoNCDT 2300.pdf") == "man--optoNCDT 2300.pdf"
    assert clean_name("../../etc/notes.md") == "notes.md"
    with pytest.raises(KnowledgeError):
        clean_name("tool.exe")


def test_split_text_keeps_every_word_and_overlaps():
    text = " ".join(f"Sentence number {n} says something about the sensor." for n in range(60))
    chunks = split_text(text, size=300, overlap=80)
    assert len(chunks) > 5 and all(len(c) <= 320 for c in chunks)
    assert "Sentence number 0 " in chunks[0] and "Sentence number 59 " in chunks[-1]
    assert chunks[1].split(".")[0] in chunks[0]  # the overlap repeats the tail of the previous passage


def test_pdf_pages_are_kept(tmp_path):
    kb = KnowledgeBase(tmp_path / "k")
    info = kb.add("manual.pdf", make_pdf([PAGE_ONE, PAGE_TWO]))
    assert info["pages"] == 2 and info["passages"] >= 2
    assert {p.page for p in kb.passages} == {1, 2}
    assert (tmp_path / "k" / "manual.pdf").exists()


def test_unreadable_files_are_reported_with_a_reason(tmp_path):
    kb = KnowledgeBase(tmp_path / "k")
    with pytest.raises(KnowledgeError, match="could not be read"):
        kb.add("broken.pdf", b"%PDF-1.4 this is not a pdf")
    with pytest.raises(KnowledgeError, match="empty"):
        kb.add("empty.txt", b"")
    with pytest.raises(KnowledgeError, match="Only PDF"):
        kb.add("run.exe", b"MZ")


def test_the_folder_is_read_at_start_and_a_bad_file_does_not_stop_the_rest(tmp_path):
    folder = tmp_path / "k"
    folder.mkdir()
    (folder / "good.md").write_text("# Drift\nWarm up the sensor for twenty minutes before measuring.", "utf-8")
    (folder / "bad.pdf").write_bytes(b"nope")
    (folder / "README.md").write_text("# not a manual", "utf-8")
    kb = KnowledgeBase(folder)
    assert list(kb.documents) == ["good.md"]
    assert kb.warnings and kb.warnings[0].startswith("bad.pdf")
    assert kb.status()["passages"] == 1


def test_keyword_search_finds_the_right_page(tmp_path):
    import asyncio
    kb = KnowledgeBase(tmp_path / "k")
    kb.add("manual.pdf", make_pdf([PAGE_ONE, PAGE_TWO]))
    hits = asyncio.run(kb.search("vibration of the target and the clamp"))
    assert hits and hits[0].passage.page == 1 and hits[0].via == "keywords"
    hits = asyncio.run(kb.search("slow drift temperature warm-up"))
    assert hits[0].passage.page == 2
    assert asyncio.run(kb.search("zzzz qqqq")) == []
    assert tokenize("Vibrations and clamps") == ["vibration", "clamp"]


def test_a_german_manual_is_found_by_keywords_for_every_kind_of_alert(tmp_path):
    import asyncio
    pages = [
        "Montage und Schwingung. Befestigen Sie den Sensor an einer steifen Halterung. Schwingungen von Sensor oder "
        "Messobjekt zeigen sich als schwankender Messwert. Verlegen Sie das Kabel fern von bewegten Teilen.",
        "Temperaturdrift. Beachten Sie eine Aufwärmzeit von 20 Minuten. Temperaturänderungen am Sensorgehäuse "
        "führen zu einer langsamen Drift des Messwerts.",
        "Messobjekt und Oberfläche. Glänzende oder verschmutzte Oberfläche und eine Neigung des Messobjekts "
        "können zu ungültigen Messwerten führen. Reinigen Sie die Oberfläche.",
    ]
    kb = KnowledgeBase(tmp_path / "k")
    kb.add("handbuch.txt", "\f".join(pages).encode("utf-8"))        # a form feed starts a new page in a text file
    for kind, page in (("vibration", 1), ("drift", 2), ("invalid", 3)):
        hits = asyncio.run(kb.search(build_query({"kind": kind, "kinds": [kind]})))
        assert hits and hits[0].passage.page == page, (kind, [h.passage.page for h in hits])
    for kind in ("vibration", "noise", "drift", "out_of_range", "invalid"):
        assert len(build_query({"kind": kind, "kinds": [kind]}).split()) > 12


def test_a_table_of_contents_is_not_a_source_but_a_normal_page_is():
    contents = "\n".join(f"{n}.{m} Chapter title number {n}{m} {'.' * 40} {n * 7 + m}" for n in range(1, 4) for m in range(1, 4))
    assert is_contents_page("Contents\n" + contents)
    assert not is_contents_page("Mechanical vibrations. Mount the sensor on a rigid bracket. " * 4)
    assert not is_contents_page("Version 1.2 ..... see page 4\nA short page.")


def test_focused_questions_find_the_short_paragraph_a_long_keyword_list_would_bury(tmp_path):
    import asyncio
    filler = ["Mounting plate drawing with dimensions, screws, cable and fixture clamp holes. " * 6,
              "Cable connection, pin assignment, installation of the connector and cable. " * 6]
    target = ("Mechanical vibrations. Particular attention must be paid to stable or vibration-damped sensor and "
              "target mounting.")
    kb = KnowledgeBase(tmp_path / "k")
    kb.add("m.txt", "\f".join([filler[0], target, filler[1]]).encode("utf-8"))
    hits = asyncio.run(kb.search_many(build_queries({"kind": "vibration", "kinds": ["vibration"]}), k=3))
    assert hits[0].passage.page == 2
    assert asyncio.run(kb.search_many([], k=3)) == []


def _embedding_handler(state: dict):
    def vector(text: str) -> list[float]:
        v = [0.0] * 64
        for word in tokenize(text):
            v[hash(word) % 64] += 1.0
        return v

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] = state.get("calls", 0) + 1
        if state.get("fail"):
            return httpx.Response(500, json={"error": {"message": "server trouble"}})
        body = json.loads(request.content)
        return httpx.Response(200, json={"data": [{"index": i, "embedding": vector(t)}
                                                  for i, t in enumerate(body["input"])]})
    return handler


def test_meaning_search_is_merged_with_keywords_and_cached(tmp_path):
    import asyncio
    state: dict = {}
    client = OpenAIClient(KEY, factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(_embedding_handler(state))))
    kb = KnowledgeBase(tmp_path / "k", client)
    kb.add("manual.pdf", make_pdf([PAGE_ONE, PAGE_TWO]))
    hits = asyncio.run(kb.search("clamp the target firmly"))
    assert hits and hits[0].via == "meaning and keywords"
    first_calls = state["calls"]
    assert first_calls >= 2 and kb.status()["embeddings"]["ready"] == len(kb.passages)
    asyncio.run(kb.search("warm-up time"))
    assert state["calls"] == first_calls + 1  # only the question is embedded again
    assert (tmp_path / "k" / ".cache" / "embeddings.npz").exists()
    again = KnowledgeBase(tmp_path / "k", client)  # a restart loads the cached vectors
    assert again.status()["embeddings"]["ready"] == len(again.passages)


def test_a_failing_embedding_call_falls_back_to_keywords(tmp_path):
    import asyncio
    state: dict = {"fail": True}
    client = OpenAIClient(KEY, factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(_embedding_handler(state))))
    kb = KnowledgeBase(tmp_path / "k", client)
    kb.add("manual.pdf", make_pdf([PAGE_ONE, PAGE_TWO]))
    hits = asyncio.run(kb.search("vibration clamp"))
    assert hits and hits[0].via == "keywords"
    assert "server trouble" in (kb.status()["embeddings"]["error"] or "")
    before = state["calls"]
    asyncio.run(kb.search("vibration clamp"))
    assert state["calls"] == before  # it does not hammer the API while it is failing


def test_error_messages_never_contain_the_key():
    assert KEY not in redact(f"Incorrect API key provided: {KEY}")
    assert redact("Incorrect API key provided: sk-proj-abcdef***ZZZZ").endswith("sk-…")


# -- the verifier --------------------------------------------------------------------------


INCIDENT = {
    "id": "INC-001", "title": "Vibration on Sensor 1 at 00:20.748", "kind": "vibration", "kinds": ["vibration"],
    "severity": "warning", "summary": "Sensor 1 picked up a 26 Hz vibration, 7.7 µm peak.",
    "numbers": {"frequency_hz": 26.0, "peak": 7.7, "times_normal_noise": 4.6, "unit": "µm"},
    "checks": [{"label": "Dominant frequency", "value": "26.0 Hz", "status": "found",
                "detail": "one clear peak, prominence 74.2 (rule needs 6)"},
               {"label": "Peak deviation", "value": "7.7 µm", "status": "found",
                "detail": "4.6 times normal noise over the last 1 s"},
               {"label": "Normal noise, learned from the first 20 s", "value": "0.80 µm", "status": "info",
                "detail": "the level the rules compare against"}],
    "unknown": ["the real tolerance limits"], "forecast_min": None,
    "suggestions": [{"text": "Check how Sensor 1 is clamped.", "reason": "One clear frequency at 26.0 Hz.", "kind": "vibration"}],
    "source": {"digest": "abc123", "part_rpm": 8.6},
}
PASSAGES = [Passage("u1", "manual.pdf", 1, PAGE_ONE), Passage("u2", "manual.pdf", 2, PAGE_TWO)]
FACTS = build_facts(INCIDENT)


def fid(label: str) -> str:
    return next(f.id for f in FACTS if label in f.text)


def draft(**changes):
    base = {"action": "Check how the sensor is clamped.", "why": "A fixed 26 Hz peak is typical for a loose fixture.",
            "facts": [fid("Dominant frequency")], "basis": "manual",
            "sources": [{"passage": "P1", "quote": "clamp both firmly and keep the cable away from moving parts"}]}
    base.update(changes)
    return {"recommendations": [base]}


def kept(d, names=("Sensor 1", "Sensor 2")):
    return verify(d, FACTS, PASSAGES, names=names)


def test_facts_are_numbered_statements_of_what_was_measured():
    assert FACTS[0].id == "F1" and "INC-001" in FACTS[0].text
    texts = " ".join(f.text for f in FACTS)
    for needle in ("26.0 Hz", "7.7 µm", "0.80 µm", "8.6 rpm", "Not known from the file"):
        assert needle in texts
    assert "vibration" in build_query(INCIDENT) and "mounting" in build_query(INCIDENT)


def test_a_well_grounded_recommendation_is_kept_with_its_page():
    recs, dropped = kept(draft())
    assert dropped == 0 and len(recs) == 1
    assert recs[0]["basis"] == "manual" and recs[0]["sources"][0]["page"] == 1 and recs[0]["id"] == "R1"


@pytest.mark.parametrize("change", [
    {"why": "Replace the clamp within 45 minutes."},                        # a number nobody measured
    {"action": "Check the clamp at 30 Hz."},                                # wrong frequency
    {"facts": ["F99"]},                                                     # a fact that does not exist
    {"facts": []},                                                          # no evidence at all
    {"sources": [{"passage": "P1", "quote": "the sensor must be cooled with liquid nitrogen at all times"}]},
    {"sources": [{"passage": "P7", "quote": "clamp both firmly and keep the cable away"}]},   # no such passage
    {"sources": [{"passage": "P2", "quote": "clamp both firmly and keep the cable away"}]},   # quote is on page 1
    {"why": "This is caused by a loose fixture."},                          # a cause stated as fact
    {"why": "The manual says so.", "sources": [], "basis": "data"},         # cites the manual without a source
    {"action": ""},
])
def test_everything_that_cannot_be_checked_is_dropped(change):
    recs, dropped = kept(draft(**change))
    assert recs == [] and dropped == 1


def test_numbers_may_be_rounded_but_not_changed():
    cited = [fid("Incident"), fid("Dominant frequency"), fid("Peak deviation"), fid("Normal noise")]
    for text in ("a 26 Hz peak", "a 26.0 Hz peak", "about 8 µm", "7.7 µm", "noise of 0.8 µm", "at 20.7 s",
                 "1 s window", "4.6 times"):
        recs, dropped = kept(draft(why=text + ".", facts=cited))
        assert dropped == 0, text
    for text in ("a 27 Hz peak", "about 10 µm", "7.6 µm", "noise of 0.9 µm", "at 20.5 s", "3 s window"):
        recs, dropped = kept(draft(why=text + ".", facts=cited))
        assert dropped == 1, text
    # any measured fact of the incident may be the source of a number, cited or not
    recs, dropped = kept(draft(why="about 8 µm."))
    assert dropped == 0


def test_sensor_names_and_incident_ids_are_labels_not_numbers():
    text = "Sensor 2 and Sensor 1 saw it too, as listed under INC-001."
    assert kept(draft(why=text))[1] == 0
    assert kept(draft(why="Sensor 3 saw it too."))[1] == 1        # a sensor that does not exist is a number nobody measured
    assert kept(draft(why=text), names=())[1] == 1                # without the names, "2" is not in the facts


def test_a_number_from_the_quote_is_allowed_and_a_data_only_item_needs_no_source():
    quote = "Allow a warm-up time of 20 minutes before you measure"
    recs, dropped = kept(draft(why="The manual asks for a warm-up of 20 minutes.",
                               sources=[{"passage": "P2", "quote": quote}]))
    assert dropped == 0 and recs[0]["sources"][0]["page"] == 2
    recs, dropped = kept(draft(sources=[], basis="data", why="Compare with a known good part."))
    assert dropped == 0 and recs[0]["basis"] == "data" and recs[0]["sources"] == []


def test_quotes_may_use_an_ellipsis_but_every_part_must_be_in_the_passage():
    assert quote_in("Mount the sensor on a rigid bracket ... clamp both firmly", PAGE_ONE)
    assert not quote_in("Mount the sensor on a rigid bracket ... clamp it with glue", PAGE_ONE)
    assert not quote_in("rigid", PAGE_ONE)  # too short to count as a quote
    assert quote_in("MOUNT the sensor   on a rigid bracket", PAGE_ONE)  # case and spacing do not matter


def test_duplicates_collapse_and_the_list_is_numbered_and_capped():
    names = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf"]
    many = {"recommendations": [draft(action=f"Check the clamp, variant {n}.")["recommendations"][0]
                                for n in names] + draft()["recommendations"] * 2}
    recs, dropped = kept(many)
    assert len(recs) == 4 and [r["id"] for r in recs] == ["R1", "R2", "R3", "R4"]
    assert kept("not a dict") == ([], 0) and kept({"recommendations": "x"}) == ([], 0)


def test_choose_model_prefers_the_newest_mini():
    ids = ["gpt-4o", "gpt-4.1-mini", "gpt-5-mini", "gpt-5", "gpt-5.2", "gpt-realtime-mini", "text-embedding-3-small",
           "gpt-4o-audio-preview", "o3"]
    assert choose_model(ids) == "gpt-5-mini"
    assert choose_model(["gpt-5.1", "gpt-4o"]) == "gpt-5.1"
    assert choose_model(["whisper-1"]) is None


# -- the whole thing over HTTP -----------------------------------------------------------


class FakeOpenAI:
    """Answers like the OpenAI API. `mode` decides what the chat model says."""

    def __init__(self) -> None:
        self.mode = "good"
        self.chat: list[dict] = []
        self.models_calls = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        if request.headers.get("authorization") != f"Bearer {KEY}":
            return httpx.Response(401, json={"error": {"message": f"Incorrect API key provided: {KEY}"}})
        if path.endswith("/models"):
            self.models_calls += 1
            return httpx.Response(200, json={"data": [{"id": i} for i in ("gpt-4o", "gpt-5-mini", "gpt-realtime-mini")]})
        if path.endswith("/embeddings"):
            return _embedding_handler({})(request)
        if path.endswith("/chat/completions"):
            self.chat.append(body)
            return self.answer(body)
        return httpx.Response(404, json={"error": {"message": "no route"}})

    def answer(self, body: dict) -> httpx.Response:
        user = body["messages"][-1]["content"]
        facts = dict(re.findall(r"^(F\d+): (.*)$", user, re.M))
        numbered = [(k, v) for k, v in facts.items() if k != "F1" and re.search(r"\d", v)]
        cited, cited_text = numbered[0] if numbered else ("F1", facts["F1"])
        number = re.search(r"\d+(?:\.\d+)?", cited_text).group(0)
        passage = re.search(r"\[P1\] .*?\n(.*?)(?:\n\n|\Z)", user, re.S)
        quote = " ".join(passage.group(1).split()[4:14]) if passage else ""
        if self.mode == "http500":
            return httpx.Response(500, json={"error": {"message": "the model is overloaded"}})
        if self.mode == "malformed":
            return self.reply("this is not json at all")
        if self.mode == "format400" and "response_format" in body:
            return httpx.Response(400, json={"error": {"message": "Unsupported parameter: response_format"}})
        good = {"action": "Check how the sensor and the target are clamped.",
                "why": f"The analysis measured {number} in this check, which fits a fixture that is not rigid.",
                "facts": [cited], "basis": "manual", "sources": [{"passage": "P1", "quote": quote}]}
        invented_number = {"action": "Replace the clamp within 4711 minutes.", "why": "The peak is large.",
                           "facts": [cited], "basis": "data", "sources": []}
        invented_quote = {"action": "Cool the housing.", "why": "The housing may be warm.", "facts": [cited],
                          "basis": "manual",
                          "sources": [{"passage": "P1", "quote": "cool the housing with liquid nitrogen"}]}
        data_only = {"action": "Repeat the measurement on a known good part and compare the peak.",
                     "why": "A reference shows whether this pattern is normal for this setup.", "facts": [cited],
                     "basis": "data", "sources": []}
        drafts = {"good": [good, invented_number, invented_quote, data_only],
                  "format400": [good], "all_bad": [invented_number, invented_quote]}[self.mode]
        return self.reply(json.dumps({"recommendations": drafts}))

    @staticmethod
    def reply(content: str) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": content}}]})


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    fake = FakeOpenAI()
    base = load_settings(BACKEND / "config" / "sensors.yaml")
    settings = dataclasses.replace(
        base, openai_api_key=KEY, cockpit_token="test-token", source_override="", text_model="",
        knowledge_dir=tmp_path_factory.mktemp("knowledge"), recommend_prefetch=True)
    app = create_app(settings, openai_factory=lambda: httpx.AsyncClient(transport=fake.transport()))
    with TestClient(app) as client:
        up = client.post("/api/knowledge/upload", content=make_pdf([PAGE_ONE, PAGE_TWO]),
                         headers={**TOKEN, "X-File-Name": "optoNCDT-manual.pdf"})
        assert up.status_code == 200, up.text
        info = client.post("/api/recording/inspect", content=make_text("german").encode("utf-8"),
                           headers={**TOKEN, "X-File-Name": "demo_recording.csv", "Content-Type": "text/csv"}).json()
        started = client.post("/api/recording/analyse", json={"mapping": info["mapping"], "rate_hz": 500}, headers=TOKEN)
        assert started.status_code == 202
        deadline = time.time() + 120
        while time.time() < deadline:
            status = client.get("/api/recording/status").json()
            if status["state"] in ("done", "error"):
                break
            time.sleep(0.2)
        assert status["state"] == "done", status
        incidents = [row["id"] for row in client.get("/api/incidents").json()["incidents"]]
        while time.time() < deadline:                      # the background preparation after the analysis
            if client.get("/api/knowledge").json()["cached"] >= len(incidents):
                break
            time.sleep(0.2)
        yield client, fake, incidents


def test_the_manual_is_listed_and_upload_needs_the_token(rig):
    client, _, _ = rig
    status = client.get("/api/knowledge").json()
    assert status["documents"][0]["name"] == "optoNCDT-manual.pdf" and status["documents"][0]["pages"] == 2
    assert status["ai"] is True and status["embeddings"]["enabled"] is True
    assert client.post("/api/knowledge/upload", content=b"x", headers={"X-File-Name": "a.md"}).status_code == 401
    bad = client.post("/api/knowledge/upload", content=b"x", headers={**TOKEN, "X-File-Name": "a.exe"})
    assert bad.status_code == 422 and "Only PDF" in bad.json()["detail"]
    assert client.delete("/api/knowledge/none.pdf", headers=TOKEN).status_code == 404


def test_a_quote_links_to_the_manual_page(rig):
    client, _, _ = rig
    served = client.get("/api/knowledge/file/optoNCDT-manual.pdf")
    assert served.status_code == 200 and served.headers["content-type"] == "application/pdf"
    assert served.content.startswith(b"%PDF") and "inline" in served.headers["content-disposition"]
    assert client.get("/api/knowledge/file/missing.pdf").status_code == 404
    assert client.get("/api/knowledge/file/..%2F..%2Fetc%2Fpasswd").status_code == 404


def test_recommendations_are_prepared_in_the_background_after_the_analysis(rig):
    client, fake, incidents = rig
    assert len(incidents) == 5 and len(fake.chat) >= 5
    first = client.get(f"/api/incidents/{incidents[0]}/recommendations").json()   # open read, no token, no model call
    assert first["status"] == "ai" and first["model"] == "gpt-5-mini"
    assert fake.models_calls == 1                                             # the model was chosen once


def test_only_checked_recommendations_reach_the_screen(rig):
    client, fake, incidents = rig
    result = client.post(f"/api/incidents/{incidents[0]}/recommendations", json={"force": True}, headers=TOKEN).json()
    assert result["status"] == "ai" and result["dropped"] == 2
    actions = [r["action"] for r in result["recommendations"]]
    assert actions == ["Check how the sensor and the target are clamped.",
                       "Repeat the measurement on a known good part and compare the peak."]
    first = result["recommendations"][0]
    assert first["basis"] == "manual" and first["sources"][0]["page"] == 1 and first["sources"][0]["doc"] == "optoNCDT-manual.pdf"
    assert result["recommendations"][1]["basis"] == "data"
    assert any(f["id"] == first["facts"][0] for f in result["facts"])
    request = fake.chat[-1]
    assert request["response_format"] == {"type": "json_object"} and "temperature" not in request
    assert "[P1] optoNCDT-manual.pdf, page 1" in request["messages"][-1]["content"]


def test_a_second_request_is_answered_from_the_cache_and_needs_the_token(rig):
    client, fake, incidents = rig
    assert client.post(f"/api/incidents/{incidents[1]}/recommendations").status_code == 401
    before = len(fake.chat)
    again = client.post(f"/api/incidents/{incidents[1]}/recommendations", headers=TOKEN).json()
    assert again["status"] == "ai" and len(fake.chat) == before
    assert client.get("/api/incidents/INC-999/recommendations").status_code == 404


@pytest.mark.parametrize("mode,needle", [
    ("http500", "overloaded"), ("malformed", "could not be read"), ("all_bad", "dropped by the checker")])
def test_when_the_ai_fails_the_rule_based_checks_are_shown_and_the_reason_is_given(rig, mode, needle):
    client, fake, incidents = rig
    fake.mode = mode
    try:
        result = client.post(f"/api/incidents/{incidents[2]}/recommendations", json={"force": True}, headers=TOKEN).json()
    finally:
        fake.mode = "good"
    assert result["status"] == "rules" and needle in result["note"]
    assert result["recommendations"] and all(r["basis"] == "rules" for r in result["recommendations"])
    assert KEY not in json.dumps(result)


def test_a_model_that_rejects_json_mode_is_asked_again_without_it(rig):
    client, fake, incidents = rig
    fake.mode = "format400"
    before = len(fake.chat)
    try:
        result = client.post(f"/api/incidents/{incidents[3]}/recommendations", json={"force": True}, headers=TOKEN).json()
    finally:
        fake.mode = "good"
    assert result["status"] == "ai" and len(fake.chat) == before + 2 and "response_format" not in fake.chat[-1]


def test_the_report_and_the_copilot_tools_carry_the_checked_recommendations(rig):
    client, _, incidents = rig
    client.post(f"/api/incidents/{incidents[0]}/recommendations", json={"force": True}, headers=TOKEN)
    page = client.get(f"/report/incident/{incidents[0]}").text
    assert "Recommended next steps" in page and "optoNCDT-manual.pdf" in page and "page 1" in page
    assert "2 drafts failed the checks" in page
    tool = client.post("/api/tools/investigate_incident", json={"incident_id": incidents[0]}).json()
    items = tool["recommendations"]["items"]
    assert tool["recommendations"]["kind"] == "ai_checked_against_the_data" and items[0]["sources"][0]["page"] == 1
    assert items[0]["based_on"] and isinstance(items[0]["based_on"][0], str)
    found = client.post("/api/tools/search_manual", json={"query": "keep the cable away from moving parts"}, headers=TOKEN).json()
    assert found["ok"] and found["passages"][0]["page"] == 1 and "speak_rule" in found
    assert client.post("/api/tools/search_manual", json={"query": "clamp"}).status_code == 401


def test_without_a_key_the_cockpit_still_answers_with_the_rule_based_checks(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        info = client.post("/api/recording/inspect", content=make_text("german").encode("utf-8"),
                           headers={**TOKEN, "X-File-Name": "demo_recording.csv", "Content-Type": "text/csv"}).json()
        client.post("/api/recording/analyse", json={"mapping": info["mapping"], "rate_hz": 500}, headers=TOKEN)
        deadline = time.time() + 120
        while time.time() < deadline and client.get("/api/recording/status").json()["state"] not in ("done", "error"):
            time.sleep(0.2)
        first = client.get("/api/incidents").json()["incidents"][0]["id"]
        result = client.post(f"/api/incidents/{first}/recommendations", headers=TOKEN).json()
        assert result["status"] == "rules" and "no OpenAI key" in result["note"] and result["recommendations"]
        tool = client.post("/api/tools/investigate_incident", json={"incident_id": first}).json()
        assert tool["recommendations"]["kind"] == "rule_based"
        assert client.get("/api/knowledge").json()["ai"] is False


def test_the_client_names_a_rejected_key_without_repeating_it():
    import asyncio

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": f"Incorrect API key provided: {KEY}"}})

    client = OpenAIClient(KEY, factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(OpenAIError) as caught:
        asyncio.run(client.request("GET", "/models"))
    assert "rejected the key" in str(caught.value) and KEY not in str(caught.value)
    with pytest.raises(OpenAIError, match="No OpenAI key"):
        asyncio.run(OpenAIClient("").request("GET", "/models"))


# -- the check script for the real machine -------------------------------------------------


def _check_settings(tmp_path, key=KEY, with_manual=True):
    folder = tmp_path / "k"
    folder.mkdir()
    if with_manual:
        (folder / "manual.pdf").write_bytes(make_pdf([PAGE_ONE, PAGE_TWO]))
    base = load_settings(BACKEND / "config" / "sensors.yaml")
    return dataclasses.replace(base, openai_api_key=key, knowledge_dir=folder, text_model="", cockpit_token="x")


def test_the_rag_check_script_is_green_against_a_fake_openai(tmp_path):
    import asyncio
    import rag_check
    fake = FakeOpenAI()
    lines: list[str] = []
    failed = asyncio.run(rag_check.run(_check_settings(tmp_path),
                                       factory=lambda: httpx.AsyncClient(transport=fake.transport()),
                                       out=lines.append))
    text = "\n".join(lines)
    assert failed == 0, text
    assert "chosen automatically" in text and "gpt-5-mini" in text and "recommendations by gpt-5-mini" in text
    assert "page 1" in text and KEY not in text


def test_the_rag_check_script_says_what_is_missing(tmp_path):
    import asyncio
    import rag_check
    lines: list[str] = []
    assert asyncio.run(rag_check.run(_check_settings(tmp_path, key=""), out=lines.append)) == 1
    assert "No OPENAI_API_KEY" in "\n".join(lines)
    empty = tmp_path / "empty"
    empty.mkdir()
    fake = FakeOpenAI()
    lines = []
    failed = asyncio.run(rag_check.run(_check_settings(empty, with_manual=False),
                                       factory=lambda: httpx.AsyncClient(transport=fake.transport()),
                                       out=lines.append))
    assert failed == 1 and "No manual found" in "\n".join(lines)
