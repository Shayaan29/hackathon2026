"""Reading recordings of any shape, and refusing what cannot be read honestly."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND / "scripts"))

from make_demo_recording import VARIANTS, make_text  # noqa: E402

from app import ingest  # noqa: E402
from app.ingest import IngestError, Mapping  # noqa: E402


def _mapping_for(variant: str, rate_hz: float | None = None) -> tuple[str, Mapping]:
    text = make_text(variant)
    info = ingest.inspect(text.encode("utf-8"))
    data = info["mapping"]
    if data["time_index"] is None:
        data["rate_hz"] = rate_hz or 1000
    return text, Mapping.from_dict(data)


def test_german_export_is_detected_completely():
    text = make_text("german")
    info = ingest.inspect(text.encode("utf-8"))
    layout = info["layout"]
    assert layout["delimiter"] == ";"
    assert layout["decimal"] == ","
    assert layout["time_column"] == 0
    assert layout["rate_hz"] == pytest.approx(1000.0)
    assert layout["data_rows"] == 130_000
    names = [s["name"] for s in info["mapping"]["sensors"]]
    assert names == ["Sensor 1", "Sensor 2"]
    assert info["mapping"]["sensors"][0]["unit"] == "µm"
    assert info["warnings"] == []


def test_header_notes_of_the_file_are_kept_verbatim():
    text, mapping = _mapping_for("german")
    rec = ingest.load(text, mapping, 500, "demo.csv")
    assert rec.metadata[0].startswith("# SIMULATED demo data")


@pytest.mark.parametrize("variant", VARIANTS)
def test_every_variant_loads_onto_the_same_grid(variant):
    text, mapping = _mapping_for(variant)
    rec = ingest.load(text, mapping, 500, variant)
    assert rec.rate_hz == 500
    assert rec.duration_s == pytest.approx(130.0, abs=0.01)
    assert set(rec.values) == {"s1", "s2"}
    assert rec.values["s1"].size == rec.values["s2"].size


def test_millimetres_are_converted_to_micrometres():
    text, mapping = _mapping_for("tab_mm_counter", rate_hz=1000)
    rec = ingest.load(text, mapping, 500, "mm")
    peak = np.nanmax(np.abs(rec.values["s1"]))
    assert 50.0 < peak < 70.0  # the planted 55 µm step, not 0.055


def test_row_counter_is_not_a_sensor():
    text = make_text("tab_mm_counter")
    info = ingest.inspect(text.encode("utf-8"))
    assert info["layout"]["counter_columns"] == [0]
    assert [s["name"] for s in info["mapping"]["sensors"]] == ["Sensor 1", "Sensor 2"]


def test_missing_time_column_asks_for_the_rate():
    text = make_text("english_rate")
    info = ingest.inspect(text.encode("utf-8"))
    assert info["layout"]["time_column"] is None
    assert any("sample rate" in w for w in info["warnings"])
    data = dict(info["mapping"])
    with pytest.raises(IngestError, match="sample rate"):
        Mapping.from_dict(data)


def test_unknown_unit_is_refused_until_it_is_chosen():
    text = make_text("german")
    data = ingest.inspect(text.encode("utf-8"))["mapping"]
    data["sensors"][0]["unit"] = "V"
    with pytest.raises(IngestError, match="not a length unit"):
        Mapping.from_dict(data)


def test_unit_without_header_is_read_as_micrometres_with_a_warning():
    text = make_text("english_rate")
    info = ingest.inspect(text.encode("utf-8"))
    assert all(s["unit"] == "µm" for s in info["mapping"]["sensors"])
    assert any("no unit in the header" in w for w in info["warnings"])


def test_empty_file_is_an_error_with_a_plain_message():
    with pytest.raises(IngestError, match="empty"):
        ingest.inspect(b"")


def test_text_without_a_table_is_refused():
    with pytest.raises(IngestError, match="No table of numbers"):
        ingest.inspect("Hello\nthis is a note\nnothing numeric here\n".encode("utf-8"))


def test_invalid_words_and_empty_cells_become_gaps():
    text, mapping = _mapping_for("gaps")
    rec = ingest.load(text, mapping, 500, "gaps")
    assert rec.sensors[1]["invalid_share"] > 0.0
    assert np.isnan(rec.values["s2"]).any()
    assert not np.isnan(rec.values["s1"]).any()


def test_decimal_comma_thousands_are_understood():
    assert ingest.parse_number("1.234,5", ",") == pytest.approx(1234.5)
    assert ingest.parse_number("-0,25", ",") == pytest.approx(-0.25)
    assert ingest.parse_number("invalid", ",") != ingest.parse_number("invalid", ",")  # NaN


def test_mapping_rejects_bad_separator_and_duplicate_columns():
    base = {"delimiter": ";", "decimal": ",", "time_index": 0, "time_unit": "s", "sensors": [
        {"index": 1, "name": "A", "unit": "µm"}, {"index": 1, "name": "B", "unit": "µm"}]}
    with pytest.raises(IngestError, match="only be used once"):
        Mapping.from_dict(base)
    with pytest.raises(IngestError, match="separator"):
        Mapping.from_dict({**base, "delimiter": "|"})
