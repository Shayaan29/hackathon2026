"""The investigation chain over HTTP: upload, analyse, incidents, report, copilot tools, restore."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND / "scripts"))

from make_demo_recording import make_text  # noqa: E402

from app.main import create_app  # noqa: E402

TOKEN = {"X-Cockpit-Token": "test-token"}


def _wait_for_job(client: TestClient, timeout_s: float = 90.0) -> dict:
    deadline = time.time() + timeout_s
    status: dict = {}
    while time.time() < deadline:
        status = client.get("/api/recording/status").json()
        if status["state"] in ("done", "error"):
            return status
        time.sleep(0.2)
    raise AssertionError(f"analysis did not finish: {status}")


def _upload(client: TestClient, text: str, name: str = "demo_recording.csv") -> dict:
    response = client.post("/api/recording/inspect", content=text.encode("utf-8"),
                           headers={**TOKEN, "X-File-Name": name, "Content-Type": "text/csv"})
    assert response.status_code == 200, response.text
    return response.json()


def test_the_whole_chain_over_http(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        info = _upload(client, make_text("german"))
        assert info["file"]["name"] == "demo_recording.csv"
        assert info["layout"]["delimiter"] == ";"

        started = client.post("/api/recording/analyse", json={"mapping": info["mapping"], "rate_hz": 500},
                              headers=TOKEN)
        assert started.status_code == 202, started.text
        status = _wait_for_job(client)
        assert status["state"] == "done", status
        assert status["result"]["stats"]["incidents"] == 5

        listing = client.get("/api/incidents").json()
        assert [row["kind"] for row in listing["incidents"]][:2] == ["vibration", "drift"]
        first = listing["incidents"][0]["id"]

        detail = client.get(f"/api/incidents/{first}", headers=TOKEN).json()
        assert detail["checks"] and detail["explanation"] and detail["snapshot"]["sensors"]

        csv_response = client.get(f"/api/incidents/{first}/csv", headers=TOKEN)
        assert csv_response.status_code == 200
        assert csv_response.headers["content-type"].startswith("text/csv")

        page = client.get(f"/report/incident/{first}")
        assert page.status_code == 200
        assert "Checks the rules ran" in page.text
        assert client.get("/report/incident/INC-999").status_code == 404

        acknowledged = client.post(f"/api/incidents/{first}/acknowledge", headers=TOKEN).json()
        assert acknowledged["status"] == "acknowledged"

        copilot_list = client.post("/api/tools/list_incidents").json()
        assert copilot_list["available"] is True and copilot_list["total"] == 5
        copilot = client.post("/api/tools/investigate_incident", json={"incident_id": "INC-005"}).json()
        assert copilot["ok"] is True and copilot["incident"]["id"] == "INC-005"
        assert "Speak only numbers" in copilot["speak_rule"]
        missing = client.post("/api/tools/investigate_incident", json={"incident_id": "INC-999"}).json()
        assert missing["ok"] is False

        # the cockpit plays the recording and the live alerts stay off, so nothing is counted twice
        assert app.state.engine.live_detection is False
        playing = client.get("/api/status").json()["recording"]
        assert playing["active"] is True and playing["started_at"] > 0 and playing["incidents"] == 5

        restored = client.post("/api/recording/restore", headers=TOKEN)
        assert restored.status_code == 200
        assert client.get("/api/incidents").json()["incidents"] == []
        assert app.state.engine.live_detection is True
        assert client.get("/api/status").json()["recording"]["active"] is False


def test_writes_need_the_cockpit_token(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.post("/api/recording/inspect", content=b"1;2\n").status_code == 401
        assert client.post("/api/recording/analyse", json={"mapping": {}}).status_code == 401
        assert client.post("/api/recording/restore").status_code == 401
        assert client.post("/api/incidents/INC-001/acknowledge").status_code == 401
        # reads stay open, like the rest of the read-only API (404 here: nothing is loaded)
        assert client.get("/api/incidents/INC-001").status_code == 404
        assert client.get("/api/incidents/INC-001/csv").status_code == 404


def test_a_bad_mapping_is_refused_with_the_reason(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        info = _upload(client, make_text("german"))
        mapping = dict(info["mapping"])
        mapping["sensors"] = [dict(mapping["sensors"][0], unit="V")]
        response = client.post("/api/recording/analyse", json={"mapping": mapping}, headers=TOKEN)
        assert response.status_code == 422
        assert "not a length unit" in response.json()["detail"]


def test_analysis_needs_a_loaded_file_first(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post("/api/recording/analyse", json={"mapping": {"sensors": []}}, headers=TOKEN)
        assert response.status_code == 422
        assert "Upload a recording first" in response.json()["detail"]


def test_an_unreadable_file_is_refused_at_upload(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post("/api/recording/inspect", content=b"nothing numeric\n",
                               headers={**TOKEN, "X-File-Name": "notes.txt"})
        assert response.status_code == 422
        assert "No table of numbers" in response.json()["detail"]
