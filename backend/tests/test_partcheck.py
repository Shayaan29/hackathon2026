"""The part check: teach one part, flag every other one."""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.buffer import SensorBuffer
from app.main import create_app
from app.partcheck import KIND, PartCheck, PartCheckError

TOKEN = {"X-Cockpit-Token": "test-token"}
RATE = 500
STEP_S = 0.25


class Bench:
    """A sensor buffer, a manual clock and the part check watching it."""

    def __init__(self, settings, tmp_path=None):
        self.sid = settings.sensors[0].id
        self.rng = np.random.default_rng(7)
        self.buf = SensorBuffer(RATE, 600, 3600)
        self.t = 1_700_000_000.0
        path = None if tmp_path is None else tmp_path / "part.json"
        self.check = PartCheck(settings.sensors, path)
        self.events = []

    def feed(self, value, seconds, noise=1.0):
        """Hold the target at ``value`` µm for ``seconds`` (or a ramp when value is a pair), watching all along."""
        end = self.t + seconds
        while self.t < end - 1e-9:
            step = min(STEP_S, end - self.t)
            n = int(round(step * RATE))
            if isinstance(value, tuple):
                a, b = value
                done = (self.t - (end - seconds)) / seconds
                values = np.linspace(a + (b - a) * done, a + (b - a) * (done + step / seconds), n)
            else:
                values = np.full(n, float(value))
            values = values + self.rng.normal(0.0, noise, n)
            self.t += step
            self.buf.append(values, self.t)
            self.events.extend(self.check.evaluate({self.sid: self.buf}, self.t))

    @property
    def state(self):
        return self.check.public(self.sid)["state"]

    def kinds(self):
        return [e.kind for e in self.events]


@pytest.fixture()
def bench(settings, tmp_path):
    return Bench(settings, tmp_path)


def test_nothing_is_flagged_before_a_part_is_taught(bench):
    bench.feed(5000, 3)
    assert bench.events == [] and bench.state == "no_reference"
    assert "Teach this part" in bench.check.public(bench.sid)["message"]


def test_teaching_remembers_the_part_and_its_band(bench):
    bench.feed(5000, 2)
    info = bench.check.learn(bench.sid, bench.buf)
    assert info["state"] == "match" and info["reference"]["parts"] == 1
    assert info["reference"]["center_um"] == pytest.approx(5000, abs=3)
    assert info["reference"]["half_width_um"] == pytest.approx(100, abs=1)      # the default margin of 0.1 mm


def test_the_taught_part_matches_and_stays_quiet(bench):
    bench.feed(5000, 2)
    bench.check.learn(bench.sid, bench.buf)
    bench.feed(5030, 4)                                                         # another good part, 30 µm higher
    assert bench.events == [] and bench.state == "match"


def test_another_part_raises_one_alert_and_only_one(bench):
    bench.feed(5000, 2)
    bench.check.learn(bench.sid, bench.buf)
    bench.feed(5000, 1)
    bench.feed(5800, 6)
    assert bench.kinds() == [KIND]
    event = bench.events[0]
    assert event.severity == "warning" and event.numbers["deviation"] == pytest.approx(800, abs=5)
    assert "Different part detected" in event.summary and "farther from the sensor" in event.summary
    assert bench.state == "different" and bench.check.is_different(bench.sid)


def test_the_alert_ends_when_the_good_part_comes_back_and_a_new_foreign_part_alerts_again(bench):
    bench.feed(5000, 2)
    bench.check.learn(bench.sid, bench.buf)
    bench.feed(5800, 3)
    bench.feed(5000, 3)
    assert bench.state == "match" and not bench.check.is_different(bench.sid)
    bench.feed(4300, 3)
    assert bench.kinds() == [KIND, KIND]
    assert "closer to the sensor" in bench.events[1].summary


def test_two_foreign_parts_in_a_row_each_get_an_alert(bench):
    bench.feed(5000, 2)
    bench.check.learn(bench.sid, bench.buf)
    bench.feed(5800, 3)
    bench.feed(6900, 3)                                                         # straight to another height
    assert bench.kinds() == [KIND, KIND]


def test_a_part_that_is_still_moving_is_never_an_alert(bench):
    bench.feed(5000, 2)
    bench.check.learn(bench.sid, bench.buf)
    bench.feed((5000, 7000), 0.8)                                               # a hand slides something in
    assert bench.events == []
    assert bench.check.public(bench.sid)["steady"] is False


def test_losing_the_target_is_not_a_different_part(bench):
    bench.feed(5000, 2)
    bench.check.learn(bench.sid, bench.buf)
    bench.buf.append(np.full(int(3 * RATE), np.nan), bench.t + 3)
    bench.t += 3
    bench.events.extend(bench.check.evaluate({bench.sid: bench.buf}, bench.t))
    assert bench.events == []


def test_an_empty_plate_is_nothing_when_taught_and_foreign_when_not(settings, tmp_path):
    taught = Bench(settings, tmp_path)
    taught.feed(11000, 2)
    taught.check.learn(taught.sid, taught.buf, kind="empty")
    taught.feed(5000, 2)
    taught.check.learn(taught.sid, taught.buf)                                  # now the part
    taught.feed(11000, 3)
    assert taught.events == [] and taught.state == "empty"
    taught.feed(5800, 3)
    assert taught.kinds() == [KIND]
    assert taught.events[0].numbers["height"] == pytest.approx(5200, abs=10)
    assert taught.events[0].numbers["reference_height"] == pytest.approx(6000, abs=10)
    assert "above the empty plate" in taught.events[0].summary

    untaught = Bench(settings)
    untaught.feed(5000, 2)
    untaught.check.learn(untaught.sid, untaught.buf)
    untaught.feed(11000, 3)
    assert untaught.kinds() == [KIND]


def test_the_height_of_the_part_is_known_once_the_empty_plate_is_taught(bench):
    bench.feed(11000, 2)
    bench.check.learn(bench.sid, bench.buf, kind="empty")
    bench.feed(5000, 2)
    info = bench.check.learn(bench.sid, bench.buf)
    assert info["height_um"] == pytest.approx(6000, abs=5)


def test_a_second_good_part_widens_the_band(bench):
    bench.feed(5000, 2)
    bench.check.learn(bench.sid, bench.buf)
    bench.feed(5060, 2)
    info = bench.check.learn(bench.sid, bench.buf, add=True)
    ref = info["reference"]
    assert ref["parts"] == 2 and ref["center_um"] == pytest.approx(5030, abs=4)
    assert ref["half_width_um"] == pytest.approx(130, abs=4)


def test_teaching_without_a_steady_reading_says_what_to_do(settings):
    moving = Bench(settings)
    moving.feed((5000, 7000), 1.5)
    with pytest.raises(PartCheckError, match="not steady"):
        moving.check.learn(moving.sid, moving.buf)
    blind = Bench(settings)
    blind.buf.append(np.full(RATE * 2, np.nan), blind.t)
    with pytest.raises(PartCheckError, match="no valid reading"):
        blind.check.learn(blind.sid, blind.buf)


def test_the_allowed_deviation_is_adjustable_and_limited(bench):
    bench.feed(5000, 2)
    bench.check.learn(bench.sid, bench.buf)
    assert bench.check.set_margin(bench.sid, 300)["reference"]["half_width_um"] == pytest.approx(300, abs=1)
    assert bench.check.set_margin(bench.sid, 1)["reference"]["margin_um"] == 10.0
    assert bench.check.set_margin(bench.sid, 1e9)["reference"]["margin_um"] == 5000.0
    bench.feed(5200, 3)
    assert bench.events == [] and bench.state == "match"                        # a wide band accepts 200 µm
    bench.check.set_margin(bench.sid, 10)
    bench.feed(5000, 2)
    bench.feed(5200, 3)
    assert bench.kinds() == [KIND]                                              # a tight one does not


def test_forgetting_clears_what_was_taught(bench):
    bench.feed(5000, 2)
    bench.check.learn(bench.sid, bench.buf)
    info = bench.check.forget(bench.sid)
    assert info["reference"] is None and info["state"] == "no_reference"
    bench.feed(5800, 3)
    assert bench.events == []


def test_what_was_taught_survives_a_restart(settings, tmp_path):
    first = Bench(settings, tmp_path)
    first.feed(5000, 2)
    first.check.learn(first.sid, first.buf)
    first.feed(11000, 2)
    first.check.learn(first.sid, first.buf, kind="empty")
    again = PartCheck(settings.sensors, tmp_path / "part.json")
    info = again.public(first.sid)
    assert info["reference"]["center_um"] == pytest.approx(5000, abs=3) and info["empty_um"] == pytest.approx(11000, abs=3)


def test_a_broken_file_is_ignored(settings, tmp_path):
    (tmp_path / "part.json").write_text("{not json", encoding="utf-8")
    assert PartCheck(settings.sensors, tmp_path / "part.json").public(settings.sensors[0].id)["reference"] is None


# -- through the whole app ---------------------------------------------------------------------------------------

@pytest.fixture()
def client(settings):
    with TestClient(create_app(settings)) as client:
        yield client


def test_the_endpoints_need_the_token_and_answer_in_plain_words(client):
    assert client.post("/api/part/s1/learn", json={}).status_code == 401
    assert client.post("/api/part/s1/forget", json={}).status_code == 401
    state = client.get("/api/part/s1").json()
    assert state["supported"] and state["state"] == "no_reference"
    assert client.post("/api/part/s1/margin", headers=TOKEN, json={"margin_um": 50}).status_code == 409
    assert client.post("/api/part/zzz/learn", headers=TOKEN, json={}).status_code == 400
    assert client.post("/api/part/s1/learn", headers=TOKEN, json={"kind": "nonsense"}).status_code == 409


def test_a_taught_part_shows_in_the_status_and_in_the_alert_state(client):
    import time

    deadline = time.time() + 20
    reply = None
    while time.time() < deadline:                       # the simulator needs a moment to fill the buffer
        reply = client.post("/api/part/s1/learn", headers=TOKEN, json={})
        if reply.status_code == 200:
            break
        time.sleep(0.5)
    assert reply is not None and reply.status_code == 200, reply.text
    status = client.get("/api/status").json()["sensors"]["s1"]
    assert status["part"]["state"] == "match" and status["part"]["reference"]["parts"] == 1
    assert "wrong_part" not in status["active"]
    assert client.post("/api/part/s1/forget", headers=TOKEN, json={}).json()["state"] == "no_reference"


def test_a_part_swap_quiets_vibration_alerts_but_real_vibration_does_not(settings, tmp_path):
    """Sliding a part in shakes the signal for a moment: that is a swap, not a fault. A steady shake is a fault."""
    bench = Bench(settings, tmp_path)
    bench.feed(3000, 3.0)
    bench.check.learn(bench.sid, bench.buf)
    bench.check.watch_swaps({bench.sid: bench.buf}, bench.t)
    assert not bench.check.swapping(bench.sid, bench.t)

    # a real 12 Hz vibration of 800 µm: no jump of the level, so it is never taken for a swap
    n = int(round(3.0 * RATE))
    shake = 3000 + 400 * np.sin(2 * np.pi * 12 * np.arange(n) / RATE)
    bench.t += 3.0
    bench.buf.append(shake, bench.t)
    bench.check.watch_swaps({bench.sid: bench.buf}, bench.t)
    assert not bench.check.swapping(bench.sid, bench.t)

    # a part slides in: the level jumps
    bench.feed(3000, 2.0)
    bench.feed((3000, 4200), 0.4)
    bench.feed(4200, 0.5)
    bench.check.watch_swaps({bench.sid: bench.buf}, bench.t)
    assert bench.check.swapping(bench.sid, bench.t)
    assert not bench.check.swapping(bench.sid, bench.t + 3.5)      # and it is over after a few seconds


def test_a_level_jump_counts_as_a_swap_even_without_a_taught_part(bench):
    """Putting the first part down, before anything is taught, is no vibration either."""
    bench.feed(3000, 2.0)
    bench.check.watch_swaps({bench.sid: bench.buf}, bench.t)
    assert not bench.check.swapping(bench.sid, bench.t)
    bench.feed((3000, 4200), 0.4)
    bench.feed(4200, 0.5)
    bench.check.watch_swaps({bench.sid: bench.buf}, bench.t)
    assert bench.check.swapping(bench.sid, bench.t)


def _vibration_event(sid, t, related=()):
    from app.detection import Event

    return Event(id=f"ev-test-{t}", sensor=sid, kind="vibration", severity="warning", t=t,
                 numbers={"frequency_hz": 12.0, "peak": 300.0, "times_normal_noise": 9.0, "unit": "µm"},
                 summary="test vibration", related_sensors=list(related))


def _held(engine, event):
    """What the engine's loop does with a vibration alert: hold it for a second and keep it out of the status."""
    engine.detector.state[event.sensor].active["vibration"] = True
    engine._held.append((event.t + 1.0, event))
    engine._mute(event, True)


def test_the_engine_drops_a_vibration_alert_that_a_level_jump_explains(settings):
    engine = create_app(settings).state.engine          # not started: no loop runs beside the test
    published = []
    engine.publish_event = published.append
    sid, now = settings.sensors[0].id, 1_700_000_000.0
    _held(engine, _vibration_event(sid, now))
    assert "vibration" not in engine.active_kinds(sid)      # not shown while it waits
    engine.partcheck.tracks[sid].last_shift = now - 0.3      # a part was put down
    engine._release_held(now + 1.1)
    assert published == []
    assert "vibration" not in engine.active_kinds(sid)      # still hidden while the rule is on
    engine.detector.state[sid].active["vibration"] = False   # the rule clears by itself
    engine._release_held(now + 5.0)
    assert engine._muted[sid] == set()
    engine.detector.state[sid].active["vibration"] = True    # a later real vibration shows again
    assert "vibration" in engine.active_kinds(sid)


def test_the_engine_publishes_a_vibration_alert_without_a_level_jump(settings):
    engine = create_app(settings).state.engine
    published = []
    engine.publish_event = published.append
    sid, now = settings.sensors[0].id, 1_700_000_000.0
    event = _vibration_event(sid, now)
    _held(engine, event)
    engine._release_held(now + 0.5)
    assert published == []                                    # not yet: it waits a second
    engine._release_held(now + 1.1)
    assert published == [event]
    assert "vibration" in engine.active_kinds(sid)


def test_the_message_does_not_repeat_the_old_verdict_while_a_new_reading_is_checked(bench):
    bench.feed(3000, 2.0)
    bench.check.learn(bench.sid, bench.buf)
    bench.feed(3000, 1.0)
    bench.feed((3000, 4200), 0.4)
    bench.feed(4200, 2.0)
    assert bench.state == "different"
    bench.feed((4200, 3000), 0.4)
    bench.feed(3000, 0.5)            # steady again, but not yet confirmed
    info = bench.check.public(bench.sid)
    assert info["state"] == "different"
    assert info["message"].startswith("Checking: it now reads")
    bench.feed(3000, 1.0)
    info = bench.check.public(bench.sid)
    assert info["state"] == "match" and info["message"].startswith("This is the taught part")
