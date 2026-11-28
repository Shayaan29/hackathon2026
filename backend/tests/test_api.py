import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.realtime import create_client_secret, session_updates

TOKEN = {"X-Cockpit-Token": "test-token"}


@pytest.fixture()
def client(settings):
    with TestClient(create_app(settings)) as c:
        time.sleep(0.3)
        yield c


def test_health_and_config(client):
    assert client.get("/api/health").json()["ok"] is True
    config = client.get("/api/config").json()
    assert [s["id"] for s in config["sensors"]] == ["s1", "s2"]
    assert config["voice"]["enabled"] is False
    assert config["token_required"] is True


def test_history_shape_and_removed_views_are_gone(client):
    h = client.get("/api/history", params={"sensors": "s1", "window_s": 300}).json()
    series = h["series"][0]
    assert h["view"] == "timeseries" and 100 < len(series["values"]) <= 1200
    for path in ("/api/spectrum", "/api/drift", "/api/shift", "/api/assistant/s1"):
        assert client.get(path, params={"sensor": "s1"}).status_code == 404, path
    assert client.post("/api/demo/tap", headers=TOKEN, json={}).status_code in (404, 405)


def test_unknown_sensor_is_a_clear_error(client):
    r = client.get("/api/history", params={"sensors": "s9"})
    assert r.status_code == 400 and "unknown sensor" in r.json()["detail"]


def test_tools(client):
    data = client.post("/api/tools/get_data", json={"sensor": "Sensor one"}).json()
    assert data["sensor"] == "s1" and data["latest"] is not None
    analysis = client.post("/api/tools/analyze", json={"sensor": "s2"}).json()
    assert analysis["observations"]


def test_phone_socket_says_hello(client):
    with client.websocket_connect("/ws/phone") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and len(hello["sensors"]) == 2


def test_voice_off_without_key(client):
    r = client.post("/api/realtime/session", headers=TOKEN)
    assert r.status_code == 503 and "OPENAI_API_KEY" in r.json()["detail"]


def test_client_secret_request(settings):
    settings = settings.__class__(**{**settings.__dict__, "openai_api_key": "sk-test"})
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"value": "ek_123", "expires_at": 1})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await create_client_secret(settings, http)

    result = asyncio.run(run())
    assert result["client_secret"] == "ek_123"
    assert seen["url"].endswith("/v1/realtime/client_secrets")
    assert seen["auth"] == "Bearer sk-test"
    session = seen["body"]["session"]
    assert session["type"] == "realtime" and session["model"] == settings.realtime_model
    essential = session_updates(settings)[0]["session"]
    assert {t["name"] for t in essential["tools"]} == {
        "get_data", "analyze", "show_chart", "stage_control", "get_part_check", "get_parts", "create_report",
        "make_chart", "explain_chart", "search_manual"}
    assert essential["audio"]["input"]["turn_detection"] is None


def test_phone_links_never_open_the_full_cockpit(client, tmp_path, monkeypatch):
    import app.main as main
    (tmp_path / "index.html").write_text("COCKPIT")
    (tmp_path / "phone.html").write_text("PHONE")
    monkeypatch.setattr(main, "FRONTEND_DIST", tmp_path)
    for path in ("/phone", "/phone/", "/phone/phone", "/phone/phone/"):
        assert client.get(path).text == "PHONE", path
    assert client.get("/").text == "COCKPIT"
    assert client.get("/some/deep/link").text == "COCKPIT"
