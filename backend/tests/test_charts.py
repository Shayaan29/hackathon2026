"""Copilot-picked charts, chart explanations and the zoomable shift timeline."""

import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

TOKEN = {"X-Cockpit-Token": "test-token"}


@pytest.fixture(scope="module")
def client():
    from conftest import BACKEND
    from app.config import load_settings
    mp = pytest.MonkeyPatch()
    mp.setenv("OPENAI_API_KEY", "")
    mp.setenv("COCKPIT_TOKEN", "test-token")
    mp.setenv("SOURCE_OVERRIDE", "")
    settings = load_settings(BACKEND / "config" / "sensors.yaml")
    with TestClient(create_app(settings)) as c:
        time.sleep(1.0)
        yield c
    mp.undo()


@pytest.mark.parametrize("chart,params", [
    ("histogram", {"sensors": "s1"}),
    ("boxplot", {"sensors": "s1", "window_s": 28800, "group_by": "hour"}),
    ("boxplot", {"sensors": "s1,s2", "group_by": "sensor"}),
    ("scatter", {"sensors": "s1,s2", "window_s": 30}),
    ("bar", {"sensors": "s1,s2", "metric": "noise"}),
])
def test_explore_charts_come_with_explanations(client, chart, params):
    data = client.get("/api/explore", params={"chart": chart, **params}).json()
    assert data["view"] == chart
    assert data["insights"] and all(s["text"] for s in data["insights"])


def test_unknown_chart_is_refused(client):
    assert client.get("/api/explore", params={"chart": "pie"}).status_code == 400


def test_histogram_reports_capability(client):
    s = client.get("/api/explore", params={"chart": "histogram", "sensors": "s1"}).json()["series"][0]
    assert sum(s["counts"]) == s["n"] and s["cpk"] > 1.33


def test_live_views_can_be_explained(client):
    for view in ("timeseries", "compare", "spectrum", "drift"):
        steps = client.get("/api/insights", params={"view": view, "sensors": "s1,s2"}).json()["insights"]
        assert steps, view


def test_shift_covers_eight_hours_with_earlier_incidents(client):
    shift = client.get("/api/shift", params={"sensor": "s1"}).json()
    assert shift["t_now"] - shift["shift_start"] > 7.9 * 3600
    assert shift["source"] == "seconds"
    assert sum(1 for e in shift["events"] if e["historic"]) >= 5
    assert [s["anchor"]["type"] for s in shift["insights"]] == ["zoom"] * len(shift["insights"])


def test_zoom_picks_the_finest_data(client):
    shift = client.get("/api/shift", params={"sensor": "s1"}).json()
    now = shift["t_now"]
    recent = client.get("/api/range", params={"sensor": "s1", "t0": now - 2, "t1": now}).json()
    assert recent["source"] == "full" and recent["resolution_s"] < 0.01
    knock = next(e for e in shift["events"] if e["historic"] and e["kind"] == "vibration")
    zoomed = client.get("/api/range", params={"sensor": "s1", "t0": knock["t"] - 0.4, "t1": knock["t"] + 1.6}).json()
    assert zoomed["source"] == "snapshot" and zoomed["resolution_s"] < 0.01
    assert max(v for v in zoomed["v"] if v is not None) > 8  # the ringing is there
    wide = client.get("/api/range", params={"sensor": "s1", "t0": now - 28000, "t1": now, "points": 1000}).json()
    assert wide["source"] == "seconds" and len(wide["t"]) <= 1000
    # the spike survives thinning: the knock's peak is still in the 8-hour view's band
    assert max(v for v in wide["hi"] if v is not None) > 8
    assert all(lo <= v <= hi for lo, v, hi in zip(wide["lo"], wide["v"], wide["hi"]) if v is not None)


def test_dent_shows_once_per_turn_as_out_of_tolerance(client):
    with client.websocket_connect("/ws/cockpit") as ws:
        ws.receive_json()
        client.post("/api/demo/dent", headers=TOKEN, json={"sensor": "s1"})
        deadline = time.time() + 15
        kinds = []
        while time.time() < deadline and "out_of_range" not in kinds:
            message = ws.receive_json()
            if message["type"] == "event":
                kinds.append(message["kind"])
    client.post("/api/demo/reset", headers=TOKEN, json={})
    assert "out_of_range" in kinds and "vibration" not in kinds


def test_knock_streak_lines_up_a_delayed_sensor():
    import math

    import numpy as np

    from app.explore import _at, _knock_streak

    rate = 500.0
    t = 1000.0 + np.arange(0, 3.0, 1 / rate)
    rng = np.random.default_rng(3)

    def ring(times, start):
        rel = np.clip(times - start, 0, None)
        return np.where(times >= start, 15 * np.exp(-rel / 0.35) * np.sin(2 * math.pi * 30 * rel), 0.0)

    for delay in (0.0, 0.010):
        a = ring(t, 1001.0) + 0.3 * rng.standard_normal(t.size)
        vb = 0.8 * ring(t, 1001.0 + delay) + 0.3 * rng.standard_normal(t.size)
        pair = {"fast": True, "t": t, "a": a, "b": _at(t, vb, t), "tb": t, "vb": vb, "dt": 1 / rate}
        for alert_at in (1001.1, 1001.9):  # the alert may fire well after the knock started
            h = _knock_streak({"t": alert_at}, pair)
            assert h["r"] > 0.95
            assert abs(h["lag_ms"] - delay * 1000) <= 2
            assert abs(h["t"] - 1001.0) < 0.05


def test_scatter_shows_a_tap_as_shared_movement(client):
    time.sleep(2.5)  # let the end of the previous test's dent leave the knock window
    with client.websocket_connect("/ws/cockpit") as ws:
        ws.receive_json()
        client.post("/api/demo/tap", headers=TOKEN, json={"sensor": "s1"})
        deadline = time.time() + 8
        seen = False
        while time.time() < deadline and not seen:
            message = ws.receive_json()
            seen = message["type"] == "event" and message["kind"] == "vibration"
    assert seen
    time.sleep(1.5)
    data = client.get("/api/explore", params={"chart": "scatter", "sensors": "s1,s2", "window_s": 20}).json()
    assert data["highlight"] and data["highlight"]["r"] > 0.9
    assert "moved together" in data["insights"][-1]["text"]


def test_shift_tour_stays_with_its_sensor(client):
    s1 = client.get("/api/shift", params={"sensor": "s1"}).json()
    s2 = client.get("/api/shift", params={"sensor": "s2"}).json()
    assert all(e["sensor"] == "s1" or "s1" in e["related_sensors"] for e in s1["events"])
    assert not any(e["kind"] == "drift" for e in s1["events"])
    assert any("drifting" in step["text"] for step in s2["insights"])
    assert "involved Sensor 1" in s1["insights"][0]["text"]


def test_histogram_never_hides_readings_outside_tolerance(client):
    client.post("/api/demo/dent", headers=TOKEN, json={"sensor": "s1"})
    try:
        deadline = time.time() + 12
        data = None
        while time.time() < deadline:
            time.sleep(1.0)
            data = client.get("/api/explore", params={"chart": "histogram", "sensors": "s1", "window_s": 20}).json()
            if data["series"][0]["outside"]:
                break
        s = data["series"][0]
        assert s["outside"] > 0 and s["worst"] > 40
        assert s["edges"][-1] >= s["worst"]  # the chart reaches the worst reading
        assert "outside the tolerance" in data["insights"][-1]["text"]
        assert not any("capable for this tolerance" in st["text"] for st in data["insights"])
    finally:
        client.post("/api/demo/reset", headers=TOKEN, json={})
