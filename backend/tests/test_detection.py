from conftest import Rig


def kinds(events):
    return [e.kind for e in events]


def test_quiet_signal_raises_no_alerts(rig: Rig):
    assert rig.run(60) == []


def test_tap_raises_one_vibration_with_related_sensor(rig: Rig):
    rig.run(5)
    rig.sources["s1"].inject("vibration", rig.t, amplitude=15, frequency_hz=30)
    rig.sources["s2"].inject("vibration", rig.t, amplitude=12, frequency_hz=30)
    tapped = rig.t
    events = rig.run(10)
    assert kinds(events) == ["vibration"]
    event = events[0]
    assert event.sensor == "s1"
    assert event.related_sensors == ["s2"]
    assert abs(event.numbers["frequency_hz"] - 30) < 1.5
    assert event.t - tapped < 1.0


def test_drift_is_reported_with_forecast(rig: Rig):
    rig.run(5)
    rig.sources["s2"].inject("drift", rig.t, rate_per_min=6.0)
    events = [e for e in rig.run(40) if e.kind == "drift"]
    assert len(events) == 1
    event = events[0]
    assert event.sensor == "s2"
    assert 4.5 < event.numbers["rate_per_min"] < 7.5
    assert event.forecast_min is not None and 4 < event.forecast_min < 9


def test_noise_burst_is_noise_not_vibration(rig: Rig):
    rig.run(5)
    rig.sources["s1"].inject("noise", rig.t, factor=4, duration_s=6)
    events = rig.run(10)
    assert "noise" in kinds(events)
    assert "vibration" not in kinds(events)


def test_dropout_reports_invalid_readings(rig: Rig):
    rig.run(5)
    rig.sources["s1"].inject("dropout", rig.t, share=0.5, duration_s=3)
    assert "invalid" in kinds(rig.run(6))


def test_fast_drift_ends_out_of_range(rig: Rig):
    rig.run(5)
    rig.sources["s1"].inject("drift", rig.t, rate_per_min=300.0)
    events = rig.run(15)
    assert "out_of_range" in kinds(events)
    oor = next(e for e in events if e.kind == "out_of_range")
    assert oor.severity == "critical"


def test_cooldown_blocks_repeated_taps(rig: Rig):
    rig.run(5)
    for _ in range(3):
        rig.sources["s1"].inject("vibration", rig.t, amplitude=15)
        rig.run(2)
    assert kinds(rig.events).count("vibration") == 1


def test_averaging_change_relearns_instead_of_alerting(rig: Rig):
    rig.run(5)
    rig.sources["s1"].apply_setting("averaging", 1)  # 4x more noise, on purpose
    rig.detector.relearn("s1", rig.t)
    events = rig.run(15)
    assert "noise" not in kinds(events)
    assert rig.detector.baseline("s1") > 2.5  # learned the new, noisier normal
