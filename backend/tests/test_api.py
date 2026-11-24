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


def test_history_spectrum_drift_shapes(client):
    h = client.get("/api/history", params={"sensors": "s1", "window_s": 300}).json()
    series = h["series"][0]
    assert h["view"] == "timeseries" and 100 < len(series["values"]) <= 1200
    c = client.get("/api/history", params={"sensors": "s1,s2", "window_s": 60}).json()
    assert c["view"] == "compare" and len(c["series"]) == 2
    s = client.get("/api/spectrum", params={"sensor": "s1"}).json()
    assert len(s["freqs"]) == len(s["amps"]) > 10
    d = client.get("/api/drift", params={"sensor": "sensor 2", "window_s": 600}).json()
    assert d["sensor"] == "s2" and len(d["values"]) > 100


def test_unknown_sensor_is_a_clear_error(client):
    r = client.get("/api/history", params={"sensors": "s9"})
    assert r.status_code == 400 and "unknown sensor" in r.json()["detail"]


def test_tools(client):
    data = client.post("/api/tools/get_data", json={"sensor": "Sensor one"}).json()
    assert data["sensor"] == "s1" and data["latest"] is not None
    analysis = client.post("/api/tools/analyze", json={"sensor": "s2"}).json()
    assert analysis["observations"]


def test_change_setting_needs_token_and_confirmation(client):
    body = {"sensor": "s1", "name": "averaging", "value": "64", "confirmed": True}
    assert client.post("/api/tools/change_setting", json=body).status_code == 401
    unconfirmed = client.post("/api/tools/change_setting", headers=TOKEN,
                              json={**body, "confirmed": False}).json()
    assert unconfirmed["ok"] is False
    done = client.post("/api/tools/change_setting", headers=TOKEN, json=body).json()
    assert done == {"ok": True, "sensor": "s1", "name": "averaging", "old": 16, "new": 64}
    bad = client.post("/api/tools/change_setting", headers=TOKEN,
                      json={**body, "value": "99999"}).json()
    assert bad["ok"] is False and "at most" in bad["error"]


def test_demo_tap_produces_event_on_websocket(client):
    with client.websocket_connect("/ws/cockpit") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello"
        assert client.post("/api/demo/tap", headers=TOKEN, json={"sensor": "s1"}).json()["ok"]
        deadline = time.time() + 25  # baseline needs ~20 s of data after start
        seen = set()
        while time.time() < deadline:
            message = ws.receive_json()
            seen.add(message["type"])
            if message["type"] == "event" and message["kind"] == "vibration":
                assert message["related_sensors"] == ["s2"]
                break
        else:
            pytest.fail(f"no vibration event; saw {seen}")
        assert "samples" in seen


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
        "get_data", "analyze", "show_chart", "stage_control", "change_setting",
        "suggest_settings", "create_report", "make_chart", "explain_chart", "show_shift",
        "list_incidents", "investigate_incident"}
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
