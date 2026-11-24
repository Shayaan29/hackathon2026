"""Playback of a loaded recording, and what the copilot is told about incidents."""

from __future__ import annotations

import numpy as np

from app import incident_tools
from app.main import create_app
from app.realtime import instructions, tools
from app.sources.recording import RecordingSource


def test_playback_loops_back_to_the_first_sample(settings):
    cfg = settings.sensors[0]
    source = RecordingSource(cfg, 10, np.arange(10.0), label="test")
    source.start()
    assert source.read(0.0)[0].size == 0          # the first call only sets the clock
    first, _ = source.read(1.0)                   # one second at 10 Hz is the whole file
    assert first.tolist() == list(range(10))
    looped, _ = source.read(1.5)                  # half a second more starts again at sample 0
    assert looped.tolist() == [0.0, 1.0, 2.0, 3.0, 4.0]


def test_backfill_gives_the_tail_and_playback_starts_at_the_beginning(settings):
    cfg = settings.sensors[0]
    source = RecordingSource(cfg, 10, np.arange(100.0), label="test")
    tail, _ = source.backfill(2.0, 0.0)
    assert tail.tolist() == list(range(80, 100))  # the charts open on the end of the file
    source.read(0.0)
    played, _ = source.read(1.0)
    assert played.tolist() == list(range(10))     # then playback continues from the start


def test_the_copilot_is_told_about_the_incident_tools(settings):
    names = [tool["name"] for tool in tools(settings)]
    assert len(names) == len(set(names)), "tool names must be unique"
    assert "list_incidents" in names and "investigate_incident" in names
    text = instructions(settings)
    assert "investigate_incident" in text and "list_incidents" in text
    assert "Never state a cause as fact" in text


def test_incident_tools_say_so_when_no_recording_is_loaded(settings):
    engine = create_app(settings).state.engine
    listing = incident_tools.list_incidents(engine)
    assert listing["ok"] is True and listing["available"] is False and listing["incidents"] == []
    detail = incident_tools.investigate(engine, None)
    assert detail["ok"] is False and "No recording is analysed yet" in detail["error"]
