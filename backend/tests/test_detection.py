from conftest import Rig


def kinds(events):
    return [e.kind for e in events]


def test_quiet_signal_raises_no_alerts(rig: Rig):
    assert rig.run(60) == []


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

