"""The whole-file analysis: planted events are found, explained honestly, and nothing is invented."""

from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND / "scripts"))

from make_demo_recording import PLANTED, make_text  # noqa: E402

from app import ingest  # noqa: E402
from app.incident_report import render_html  # noqa: E402
from app.incidents import analyse, excerpt_csv  # noqa: E402
from app.investigate import load_playbook  # noqa: E402


@pytest.fixture
def analysis(settings):
    text = make_text("german")
    info = ingest.inspect(text.encode("utf-8"))
    mapping = ingest.Mapping.from_dict(info["mapping"])
    rec = ingest.load(text, mapping, 500, "demo_recording.csv")
    source = {"file": "demo_recording.csv", "digest": "test", "rate_hz": 500, "rows": rec.rows,
              "duration_s": rec.duration_s, "part_rpm": None, "metadata": rec.metadata,
              "analysed_at": "test"}
    return analyse(rec, settings, playbook=load_playbook(), part_rpm=0.0, source=source)


def test_every_planted_event_is_found_on_the_right_sensor(analysis):
    for planted in PLANTED:
        sensor = f"Sensor {planted['sensor']}"
        matches = [inc for inc in analysis.incidents
                   if planted["kind"] in inc["kinds"] and inc["sensor_name"] == sensor
                   and planted["from_s"] - 1.0 <= inc["t"] <= planted["to_s"] + 2.0]
        assert matches, f"{planted} was not found"


def test_incidents_are_in_time_order_with_stable_ids(analysis):
    times = [inc["t"] for inc in analysis.incidents]
    assert times == sorted(times)
    assert [inc["id"] for inc in analysis.incidents] == [f"INC-{i:03d}" for i in range(1, len(times) + 1)]


def test_the_shared_wave_shows_no_lag_between_sensors(analysis):
    first = analysis.incidents[0]
    other = next(c for c in first["checks"] if c["key"] == "other_s2")
    assert other["status"] == "found"
    assert "lag +0.000 s" in other["detail"]


def test_missing_speed_is_reported_as_unknown_not_guessed(analysis):
    for inc in analysis.incidents:
        assert inc["location"]["part_angle_deg"] is None
        assert any("rpm" in item for item in inc["unknown"])
        assert "°" not in " ".join(inc["explanation"]) or "rpm" in " ".join(inc["explanation"])


def test_tolerance_placeholder_is_stated_on_every_incident(analysis):
    for inc in analysis.incidents:
        tol = next(c for c in inc["checks"] if c["key"] == "tolerance")
        assert "placeholder" in tol["detail"]
        assert any("tolerance" in item for item in inc["unknown"])


def test_suggestions_have_no_unfilled_placeholders_and_state_a_reason(analysis):
    for inc in analysis.incidents:
        for suggestion in inc["suggestions"]:
            assert "{" not in suggestion["text"] and "}" not in suggestion["text"]
            assert "{" not in suggestion["reason"]
            assert suggestion["reason"]


def test_suggestions_are_checks_not_causes(analysis):
    banned = ("the cause is", "caused by", "is broken", "is worn", "failed")
    for inc in analysis.incidents:
        text = " ".join(s["text"] + " " + s["reason"] for s in inc["suggestions"]).lower()
        assert not any(word in text for word in banned), text


def test_the_explanation_says_it_is_not_a_diagnosis(analysis):
    for inc in analysis.incidents:
        assert any("not a diagnosis" in line for line in inc["explanation"])


def test_a_large_step_is_flagged_as_a_possible_vibration_artifact(analysis):
    jump = next(inc for inc in analysis.incidents if inc["sensor_name"] == "Sensor 1" and inc["t"] > 117)
    assert any(c["key"].endswith("level_step") for c in jump["checks"])
    assert any("check the plot" in line for line in jump["explanation"])


def test_the_snapshot_covers_the_window_and_is_json_safe(analysis):
    inc = analysis.incidents[0]
    snap = inc["snapshot"]
    assert snap["rate_hz"] == 500
    assert len(snap["sensors"]) == 2
    assert snap["sensors"][0]["values"]
    assert inc["window"]["from_s"] <= inc["t"] <= inc["window"]["to_s"]
    assert all(v is None or isinstance(v, float) for v in snap["sensors"][1]["values"])


def test_excerpt_csv_has_a_header_and_one_row_per_sample(analysis):
    inc = analysis.incidents[0]
    rows = list(csv.reader(io.StringIO(excerpt_csv(inc)), delimiter=";"))
    assert rows[0][0] == "time_s"
    assert rows[0][1].startswith("Sensor 1 [")
    assert len(rows) - 1 == len(inc["snapshot"]["sensors"][0]["values"])


def test_report_is_self_contained_and_names_the_checks(analysis):
    inc = analysis.incidents[0]
    page = render_html(inc, team="Team Test", title_prefix="KI Sensor Cockpit")
    assert "<script" not in page and "http" not in page.replace("http://www.w3.org/2000/svg", "")
    assert inc["id"] in page
    assert "Checks the rules ran" in page
    assert "Not known from the file" in page
    assert "Team Test" in page


def test_the_report_marks_a_simulated_file_clearly(analysis):
    inc = analysis.incidents[0]
    page = render_html(inc)
    assert "Simulated demo data" in page
