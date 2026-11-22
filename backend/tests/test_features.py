"""Setup assistant, incident reports and phone shakes, through the real API."""

import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

TOKEN = {"X-Cockpit-Token": "test-token"}


@pytest.fixture()
def client(settings):
    with TestClient(create_app(settings)) as c:
        time.sleep(0.3)
        yield c


def wait_for(ws, kind: str, timeout: float = 25.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        message = ws.receive_json()
        if message["type"] == kind:
            return message
    raise AssertionError(f"no {kind} message within {timeout} s")


def test_assistant_fixes_a_misconfigured_sensor(client):
    assert client.get("/api/assistant/s1").json()["status"] == "ok"
    assert client.post("/api/demo/misconfigure", headers=TOKEN, json={"sensor": "s1"}).json()["new"] == 2
    time.sleep(3.2)  # three seconds of noisy data
    plan = client.get("/api/assistant/s1").json()
    assert plan["status"] == "improve"
    avg = next(p for p in plan["proposal"] if p["name"] == "averaging")
    assert avg["from"] == 2 and avg["to"] > 2
    assert plan["expected"]["noise"] < plan["measured"]["noise"]
    # the preview really is smoother
    assert len(plan["preview"]["before"]) == len(plan["preview"]["after"]) > 100
    assert client.post("/api/assistant/s1/apply").status_code == 401
    applied = client.post("/api/assistant/s1/apply", headers=TOKEN).json()
    assert applied["ok"] and applied["applied"][0]["new"] == avg["to"]
    # reset puts the configured settings back
    client.post("/api/demo/reset", headers=TOKEN, json={})
    assert client.post("/api/tools/get_data", json={"sensor": "s1"}).json()["settings"]["averaging"] == 16


def test_report_for_a_vibration(client):
    assert client.get("/api/reports/latest").status_code == 404
    with client.websocket_connect("/ws/cockpit") as ws:
        ws.receive_json()
        client.post("/api/demo/tap", headers=TOKEN, json={"sensor": "s1"})
        event = wait_for(ws, "event")
    time.sleep(1.0)
    rep = client.get("/api/reports/latest").json()
    assert rep["id"] == event["id"] and rep["title"].startswith("Vibration")
    assert {c["type"] for c in rep["charts"]} >= {"signal", "spectrum"}
    assert any("fixture" in c for c in rep["causes"]) and rep["actions"]
    assert "Priority" in rep["ticket"]
    page = client.get(f"/report/{event['id']}")
    assert page.status_code == 200 and "<svg" in page.text and "Likely causes" in page.text
    tool = client.post("/api/tools/create_report", json={}).json()
    assert tool["ok"] and tool["url"] == f"/report/{event['id']}"


def test_phone_shake_knocks_the_fixture(client):
    with client.websocket_connect("/ws/phone") as phone, client.websocket_connect("/ws/cockpit") as cockpit:
        phone.receive_json()
        cockpit.receive_json()
        phone.send_text('{"type": "shake", "name": "  Dr. Weber  ", "intensity": 1.4}')
        assert wait_for(phone, "shake_ack")["ok"] is True
        shake = wait_for(cockpit, "shake")
        assert shake["name"] == "Dr. Weber" and shake["sensor"] == "s1"
        phone.send_text('{"type": "shake", "name": "x"}')
        busy = wait_for(phone, "shake_ack")
        assert busy["ok"] is False and busy["reason"] == "busy"
        event = wait_for(cockpit, "event")
        assert event["kind"] == "vibration" and "s2" in event["related_sensors"]
