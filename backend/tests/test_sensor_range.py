"""A sensor with a known data sheet: positions, limits and texts follow the ILD5500-10 operating instructions."""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest
from conftest import Rig
from fastapi.testclient import TestClient

from app import realtime
from app.config import ConfigError, Settings, load_settings
from app.main import create_app
from app.sensor_specs import CATALOGUE, find_spec, invalid_hint, known_models, main_reason, reasons_text

TOKEN = {"X-Cockpit-Token": "test-token"}

SENSOR = """\
cockpit:
  title: "KI Sensor Cockpit"
  team: "Mega Mind"
engine:
  analysis_rate_hz: 500
  fast_buffer_s: 120
  slow_buffer_s: 3600
sensors:
  - id: s1
    name: "ILD5500"
    model: "{model}"
    tolerance: {tolerance}
    nominal: auto
    source:
{source}
"""


def write_config(tmp_path: Path, model: str = "optoNCDT ILD5500-10", tolerance: str = "measuring_range",
                 source: str = "      type: simulator") -> Path:
    path = tmp_path / "sensors.yaml"
    path.write_text(SENSOR.format(model=model, tolerance=tolerance, source=source), encoding="utf-8")
    return path


@pytest.fixture()
def ranged(settings: Settings, tmp_path: Path) -> Settings:
    """One simulated sensor that is configured like the real ILD5500-10: limits = its measuring range."""
    return load_settings(write_config(tmp_path))


# -- the data sheet ------------------------------------------------------------------------------------------

CITED = re.compile(r"(manual|data sheet) pp?\. \d")


def test_ild5500_10_values_are_those_of_the_manual():
    spec = find_spec("optoNCDT ILD5500-10")
    assert spec is not None and spec.model == "ILD5500-10"
    assert (spec.low_um, spec.mid_um, spec.high_um) == (0.0, 5000.0, 10000.0)
    assert (spec.ext_low_um, spec.ext_high_um) == (-2500.0, 12500.0)
    assert (spec.start_mm, spec.end_mm) == (30, 40)
    assert spec.linearity_um == 1.5
    assert spec.range_text == "0 to 10 mm" and spec.extended_text == "-2.5 to 12.5 mm"


@pytest.mark.parametrize("text", ["optoNCDT ILD5500-10", "ILD 5500-10", "ild5500-10", "Micro-Epsilon ILD-5500-10 laser"])
def test_the_model_is_recognised_from_the_label_text(text):
    assert find_spec(text) is not None and find_spec(text).model == "ILD5500-10"


@pytest.mark.parametrize("text", ["", "mystery sensor", "ILD1900-10", "ILD5500"])
def test_an_unknown_model_gets_no_data_sheet(text):
    assert find_spec(text) is None


def test_zone_follows_the_state_led_of_the_sensor():
    spec = find_spec("ILD5500-10")
    assert spec is not None
    assert [spec.zone(x).name for x in (0.0, 5000.0, 10000.0)] == ["standard"] * 3
    assert spec.zone(5000.0).led == "green"
    assert spec.zone(-1000.0).name == "before" and spec.zone(-1000.0).led == "flashing green"
    assert spec.zone(11000.0).name == "after"
    assert spec.zone(13000.0).name == "outside" and spec.zone(13000.0).led == "red"
    assert spec.zone(-2600.0).name == "outside"


def test_every_model_has_ordered_limits_and_cites_the_manual():
    assert known_models()
    for spec in CATALOGUE.values():
        assert spec.ext_low_um < spec.low_um < spec.mid_um < spec.high_um < spec.ext_high_um
        rows = spec.datasheet_rows(live_rate_khz=20.0)
        assert rows and all({"label", "value", "page"} <= set(r) and r["page"] > 0 for r in rows)


def test_invalid_reasons_are_explained_in_plain_words():
    reasons = {"no_peak": 0.8, "after_range": 0.2}
    assert main_reason(reasons) == "no_peak"
    text = reasons_text(reasons)
    assert text.startswith("mostly no peak found") and "20 % target beyond" in text
    assert "extended measuring range" in invalid_hint("after_range")
    assert "-2.5 to 12.5 mm" in invalid_hint("before_range", find_spec("ILD5500-10"))
    assert reasons_text(None) == "" and main_reason({}) is None


# -- configuration -------------------------------------------------------------------------------------------

def test_measuring_range_becomes_the_tolerance_and_positions_are_shown_in_mm(ranged):
    cfg = ranged.sensors[0]
    assert (cfg.tolerance.low, cfg.tolerance.high) == (0.0, 10000.0)
    assert cfg.nominal == 5000.0 and cfg.tolerance_kind == "measuring_range"
    assert cfg.limit_word == "measuring range"
    assert cfg.pos(5001.234) == "5.0012 mm" and cfg.pos(-120.5) == "-0.1205 mm"
    public = cfg.public()
    assert public["display"] == {"unit": "mm", "scale": 0.001, "decimals": 4}
    assert public["spec"]["zones_um"] == {"ext_low": -2500.0, "low": 0.0, "mid": 5000.0, "high": 10000.0,
                                          "ext_high": 12500.0}
    assert public["tolerance_kind"] == "measuring_range" and public["limit_word"] == "measuring range"


def test_a_part_tolerance_still_works_as_before(settings):
    cfg = settings.sensors[0]
    assert cfg.tolerance_kind == "part" and cfg.display is None and cfg.limit_word == "tolerance"
    assert cfg.pos(6.0) == "+6.0 µm"
    assert cfg.public()["spec"] is None and cfg.public()["display"] is None


def test_measuring_range_needs_a_known_model(settings, tmp_path):
    with pytest.raises(ConfigError, match="known sensor model"):
        load_settings(write_config(tmp_path, model="mystery sensor"))


def test_measuring_range_counts_from_the_start_of_the_range(settings, tmp_path):
    live = "      type: ild5500\n      host: 127.0.0.1\n      port: 1024\n      reference_mm: {ref}"
    with pytest.raises(ConfigError, match="range_start"):
        load_settings(write_config(tmp_path, source=live.format(ref="mid")))
    ok = load_settings(write_config(tmp_path, source=live.format(ref="range_start")))
    assert ok.sensors[0].source["range_mm"] == 10 and ok.sensors[0].source["reference_mm"] == "range_start"


def test_a_part_tolerance_can_still_be_typed_in_micrometres(settings, tmp_path):
    cfg = load_settings(write_config(tmp_path, tolerance="{low: 4950.0, high: 5050.0}")).sensors[0]
    assert cfg.tolerance_kind == "part" and (cfg.tolerance.low, cfg.tolerance.high) == (4950.0, 5050.0)


def test_the_shipped_live_file_describes_the_real_sensor(settings, monkeypatch):
    monkeypatch.delenv("LIVE_SENSOR_HOST", raising=False)
    live = load_settings(Path(__file__).resolve().parent.parent / "config" / "sensors.live.yaml")
    cfg = live.sensors[0]
    assert cfg.spec is not None and cfg.spec.model == "ILD5500-10"
    assert cfg.tolerance_kind == "measuring_range" and (cfg.tolerance.low, cfg.tolerance.high) == (0.0, 10000.0)
    assert cfg.source["host"] == "169.254.168.150" and cfg.source["reference_mm"] == "range_start"


# -- alerts in the words of the data sheet ---------------------------------------------------------------------

def test_leaving_the_measuring_range_is_reported_with_the_extended_range(ranged):
    rig = Rig(ranged)
    rig.run(5)
    rig.sources["s1"].inject("drift", rig.t, rate_per_min=600_000.0)   # 10 mm per second: the end of the range
    events = [e for e in rig.run(10) if e.kind == "out_of_range"]
    assert events, "the target left the measuring range but no alert fired"
    event = events[0]
    assert event.severity == "critical"
    assert "outside its measuring range" in event.summary and "0 to 10 mm" in event.summary
    assert " mm " in event.summary and "µm" not in event.summary.split("range:")[1]
    assert event.numbers["zone"] in ("after", "outside") and event.numbers["extended_range"] == "-2.5 to 12.5 mm"
    assert event.numbers["limit"] == 10000.0


def test_a_part_tolerance_alert_keeps_its_old_wording(settings):
    rig = Rig(settings)
    rig.run(5)
    rig.sources["s1"].inject("drift", rig.t, rate_per_min=300.0)
    oor = next(e for e in rig.run(15) if e.kind == "out_of_range")
    assert "is out of tolerance" in oor.summary and "µm" in oor.summary


# -- the cockpit through its API ---------------------------------------------------------------------------------

@pytest.fixture()
def client(ranged):
    with TestClient(create_app(ranged)) as c:
        time.sleep(0.4)
        yield c


def inject(client, kind: str, **params) -> None:
    """Make the simulated sensor of the test do something (the cockpit itself has no such controls)."""
    client.app.state.engine.sources["s1"].inject(kind, time.time(), **params)


def test_config_and_charts_carry_the_display_unit(client):
    sensor = client.get("/api/config").json()["sensors"][0]
    assert sensor["display"]["unit"] == "mm" and sensor["spec"]["model"] == "ILD5500-10"
    history = client.get("/api/history", params={"sensors": "s1", "window_s": 60}).json()["series"][0]
    assert history["display"]["unit"] == "mm" and history["limit_word"] == "measuring range"
    assert history["unit"] == "µm" and all(4900 < v < 5100 for v in history["values"] if v is not None)


def test_the_copilot_reads_positions_in_mm_and_names_the_data_sheet(client):
    data = client.post("/api/tools/get_data", json={"sensor": "s1"}).json()
    position = data["position"]
    assert position["latest"].endswith(" mm") and position["state_led"] == "green"
    assert position["limits"] == "0.0000 mm to 10.0000 mm (measuring range)"
    assert position["where"] == "inside the measuring range (0 to 10 mm)"
    assert "micrometres" in position["note"]
    assert "ILD5500-10" in data["datasheet"] and "±1.5 µm" in data["datasheet"]
    assert data["unit"] == "µm" and 4900 < data["latest"] < 5100      # the signal itself stays in µm
    analysis = client.post("/api/tools/analyze", json={"sensor": "s1"}).json()
    assert analysis["observations"] == [
        "Nothing unusual: the target is inside the measuring range and the readings are valid."]


def wait_for_event(client, kind: str, seconds: float = 15.0) -> dict:
    deadline = time.time() + seconds
    while time.time() < deadline:
        found = [e for e in client.get("/api/events").json()["events"] if e["kind"] == kind]
        if found:
            return found[-1]
        time.sleep(0.4)
    raise AssertionError(f"no {kind} alert within {seconds:.0f} s")


def test_an_invalid_readings_report_cites_the_manual_and_shows_the_data_sheet(client):
    inject(client, "dropout", share=0.6, duration_s=6.0)
    event = wait_for_event(client, "invalid")
    inject(client, "reset")
    rep = client.get(f"/api/reports/{event['id']}").json()
    assert rep["datasheet"]["title"] == "optoNCDT ILD5500-10" and len(rep["datasheet"]["rows"]) >= 6
    assert all(row["page"] > 0 for row in rep["datasheet"]["rows"])
    assert any(CITED.search(text) for text in rep["causes"]) and CITED.search(rep["actions"][-1])
    assert "setup" not in rep
    html = client.get(f"/report/{event['id']}").text
    assert "optoNCDT ILD5500-10" in html and "data sheet p. 6" in html and "Linearity" in html


def test_the_report_of_a_leaving_target_speaks_of_the_measuring_range(client):
    inject(client, "drift", rate_per_min=600_000.0)
    event = wait_for_event(client, "out_of_range")
    inject(client, "reset")
    assert "outside its measuring range" in event["summary"] and event["numbers"]["range"] == "0 to 10 mm"
    rep = client.get(f"/api/reports/{event['id']}").json()
    text = " ".join(rep["causes"] + rep["actions"])
    assert "measuring range" in text and "manual p." in text
    html = client.get(f"/report/{event['id']}").text
    assert "0 to 10 mm" in html and "-2.5 to 12.5 mm" in html and "mm" in html
    assert rep["title"].startswith("Out of measuring range on ")
    assert "Out of measuring range" in html and "Out of tolerance" not in html


def test_the_report_title_keeps_the_tolerance_wording_for_a_part_tolerance():
    from app.report import kind_title
    assert kind_title("out_of_range") == "Out of tolerance"
    assert kind_title("out_of_range", "tolerance") == "Out of tolerance"
    assert kind_title("out_of_range", "measuring range") == "Out of measuring range"
    assert kind_title("wrong_part", "measuring range") == "Different part"


def test_chart_explanations_speak_millimetres_and_the_measuring_range(client):
    steps = client.get("/api/insights", params={"view": "timeseries", "sensors": "s1", "window_s": 120}).json()["insights"]
    texts = [step["text"] for step in steps]
    assert any("mostly stayed between 4.99" in t or "mostly stayed between 5.00" in t for t in texts)
    assert texts[-1].startswith("Right now it reads") and texts[-1].endswith("inside the measuring range (0 to 10 mm).")
    assert all("tolerance" not in t for t in texts)

    hist = client.get("/api/explore", params={"chart": "histogram", "sensors": "s1", "window_s": 120}).json()
    series = hist["series"][0]
    assert series["capability"] is False and series["limit_word"] == "measuring range"
    assert series["display"]["unit"] == "mm" and series["unit"] == "µm"
    said = " ".join(step["text"] for step in hist["insights"])
    assert "of the way into the 0 to 10 mm measuring range" in said and "three standard deviations" in said
    assert "Cpk" not in said and "tolerance" not in said


# -- the voice copilot ---------------------------------------------------------------------------------------------

def test_the_copilot_is_told_the_data_sheet_and_to_speak_millimetres(ranged):
    text = realtime.instructions(ranged)
    assert "ILD5500-10" in text and "manual p." in text.replace("manual page", "manual p.")
    assert "millimetre" in text.lower()
    assert "0 to 10 mm" in text and "-2.5 to 12.5 mm" in text


def test_the_catalogue_follows_the_newer_data_sheet():
    assert [CATALOGUE[m].linearity_um for m in (10, 25, 50, 100, 200)] == [1.5, 3.75, 7.5, 20.0, 60.0]
    assert CATALOGUE[10].temperature_um_per_k == pytest.approx(0.5)
    assert CATALOGUE[100].temperature_um_per_k == pytest.approx(20.0)
    assert CATALOGUE[10].spot_mmr_um == (70, 45) and CATALOGUE[10].smallest_spot_at_mm == 35.6
    rows = {r["label"]: r for r in CATALOGUE[10].datasheet_rows()}
    assert rows["Linearity"]["source"] == "optoNCDT 5500 data sheet" and rows["Linearity"]["page"] == 6
    assert rows["Warm-up"]["source"] == "optoNCDT 5500 operating instructions"


@pytest.mark.parametrize("text", ["optoNCDT ILD5500-10DR", "ILD5500-10 DR", "ild5500-10-dr"])
def test_a_dr_variant_is_not_read_as_the_plain_model(text):
    assert find_spec(text) is None
