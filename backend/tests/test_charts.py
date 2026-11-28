"""Copilot-picked charts and chart explanations."""

import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

TOKEN = {"X-Cockpit-Token": "test-token"}


@pytest.fixture(scope="module")
def client():
    from conftest import TESTS
    from app.config import load_settings
    mp = pytest.MonkeyPatch()
    mp.setenv("OPENAI_API_KEY", "")
    mp.setenv("COCKPIT_TOKEN", "test-token")
    settings = load_settings(TESTS / "sensors.yaml")
    with TestClient(create_app(settings)) as c:
        time.sleep(1.0)
        yield c
    mp.undo()


@pytest.mark.parametrize("chart,params", [
    ("histogram", {"sensors": "s1"}),
    ("boxplot", {"sensors": "s1", "window_s": 900, "group_by": "10min"}),
    ("boxplot", {"sensors": "s1", "window_s": 600, "group_by": "minute"}),
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


def test_the_live_signal_can_be_explained_and_removed_views_are_refused(client):
    steps = client.get("/api/insights", params={"view": "timeseries", "sensors": "s1"}).json()["insights"]
    assert steps
    for view in ("compare", "spectrum", "drift"):
        assert client.get("/api/insights", params={"view": view, "sensors": "s1"}).status_code == 400


def test_histogram_never_hides_readings_outside_tolerance(settings):
    import numpy as np

    from app import explore
    from app.main import create_app as make

    engine = make(settings).state.engine          # not started: the buffer holds exactly what the test puts in
    rng = np.random.default_rng(3)
    values = rng.normal(0.0, 1.0, 20 * 500)
    values[5000:5100] = -62.0                      # a dent: 0.2 s far outside the -40 to +40 µm tolerance
    engine.buffers["s1"].append(values, time.time())
    data = explore.histogram(engine, ["s1"], 20.0)
    s = data["series"][0]
    assert s["outside"] > 0 and s["worst"] < -40
    assert s["edges"][0] <= s["worst"]             # the chart reaches the worst reading
    assert "outside the tolerance" in data["insights"][-1]["text"]
    assert not any("capable for this tolerance" in st["text"] for st in data["insights"])
