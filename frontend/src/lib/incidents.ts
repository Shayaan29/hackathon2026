// What the stage and the twin show for one incident. Pure functions, no state.
import type {
  IncidentChartData, IncidentDetail, InsightStep, RecordingPublic, TwinSpot,
} from "../types";

/**
 * The incident as a stage chart: one trace per sensor on the recording's own time
 * axis, the incident's sensor first. The explanation steps become the tour: the
 * first step points at the rule window, the second at the measured peak.
 */
export function incidentChart(detail: IncidentDetail): IncidentChartData {
  const sensors = [...detail.snapshot.sensors].sort((a, b) =>
    a.id === detail.sensor ? -1 : b.id === detail.sensor ? 1 : 0);
  const series = sensors.map((s) => ({
    sensor: s.id,
    name: s.name,
    unit: s.unit,
    tolerance: s.tolerance,
    t0: detail.snapshot.t0,
    dt: 1 / detail.snapshot.rate_hz,
    values: s.values,
  }));

  const own = sensors.find((s) => s.id === detail.sensor);
  const index = Math.round((detail.t - detail.snapshot.t0) * detail.snapshot.rate_hz);
  const peak = own && index >= 0 && index < own.values.length ? own.values[index] : null;

  const insights: InsightStep[] = detail.explanation.map((text) => ({ text, anchor: null }));
  if (insights[0]) {
    insights[0].anchor = { type: "xspan", t0: detail.highlight.from_s, t1: detail.highlight.to_s, label: "rule window" };
  }
  if (insights[1] && peak !== null) {
    insights[1].anchor = { type: "point", x: detail.t, y: peak, sensor: detail.sensor, label: "measured" };
  }

  return {
    view: "incident",
    id: detail.id,
    title: detail.title,
    kind: detail.kind,
    severity: detail.severity,
    sensor: detail.sensor,
    t: detail.t,
    t_text: detail.t_text,
    highlight: detail.highlight,
    series,
    insights,
  };
}

/**
 * Where the twin puts its spot. The angle is only drawn when the part speed was
 * entered: without it the twin marks the sensor and says why the spot is missing.
 */
export function twinSpot(detail: IncidentDetail, recording: RecordingPublic | null | undefined): TwinSpot | null {
  if (!recording?.active || recording.started_at === undefined || !recording.duration_s) return null;
  const angleKnown = detail.location.part_angle_deg !== null;
  return {
    incidentId: detail.id,
    sensor: detail.sensor,
    t: detail.t,
    severity: detail.severity,
    origin: recording.started_at,
    duration: recording.duration_s,
    partRpm: angleKnown ? detail.source.part_rpm : null,
  };
}

/** One line for the list and the copilot: "INC-002 · Vibration on Sensor 1 at 00:00:20.0". */
export function incidentLine(detail: { id: string; title: string }): string {
  return `${detail.id} · ${detail.title}`;
}

/** The key under which an incident's recommendations are kept: a new recording never reuses an old answer. */
export function recKey(detail: { id: string; source: { digest: string } }): string {
  return `${detail.source.digest}:${detail.id}`;
}
