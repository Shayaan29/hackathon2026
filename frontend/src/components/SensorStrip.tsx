// Live readouts under the twin: value, a go/no-go gauge, noise and the part check.
import { useEffect, useRef } from "react";
import { controller } from "../controller";
import { KIND_LABEL, num, signed } from "../lib/format";
import { series } from "../lib/live";
import { useStore } from "../state/store";
import { pos, shownFor, zoneOf, ZONE_WORDS, type Shown } from "../lib/units";
import type { LiveStatus, SensorInfo, SensorStatus } from "../types";

const SPAN = 1.3; // the gauge shows 1.3 times the tolerance band

/** The gauge covers the extended measuring range of a sensor with a data sheet, otherwise 1.3 times the tolerance. */
function gaugeAxis(sensor: SensorInfo): { min: number; width: number } {
  const z = sensor.spec?.zones_um;
  if (z && sensor.tolerance_kind === "measuring_range") return { min: z.ext_low, width: z.ext_high - z.ext_low };
  const { low, high } = sensor.tolerance;
  const width = (high - low) * SPAN;
  return { min: (low + high) / 2 - width / 2, width };
}

/** A tick label on the gauge: the limit in the shown unit, without the unit. */
function tick(v: number, shown: Shown, edge: "low" | "high"): string {
  if (shown.display) return String(Number((v * shown.display.scale).toFixed(2))).replace("-", "−");
  return edge === "low" ? num(v, 0) : signed(v, 0);
}

interface Els { value: HTMLElement | null; needle: HTMLElement | null; zone: HTMLElement | null }

function kilohertz(hz: number | null): string {
  if (!hz) return "";
  return hz >= 1000 ? `${(hz / 1000).toFixed(hz % 1000 === 0 ? 0 : 1)} kHz` : `${hz} Hz`;
}

/** Connection of a real sensor: what is live, or in plain words what is wrong. */
function LiveLine({ live }: { live: LiveStatus }) {
  const ok = live.state === "streaming";
  const parts = ok
    ? ["Live sensor", kilohertz(live.rate_hz), live.host,
       live.reference && live.reference !== "range_start" ? `zero: ${live.reference}` : "",
       live.intensity_percent !== null ? `reflectance ${live.intensity_percent} %` : "",
       live.temperature_c !== null ? `${live.temperature_c} °C` : ""]
    : [live.message];
  return (
    <div className={`readout-live readout-live-${live.state}`} title={live.message}>
      <i aria-hidden="true" />
      {parts.filter(Boolean).map((part) => <span key={part}>{part}</span>)}
    </div>
  );
}

function Readout({ sensor, register }: {
  sensor: SensorInfo;
  register: (id: string, els: Els | null) => void;
}) {
  const status = useStore((s) => s.status?.sensors[sensor.id]);
  const valueRef = useRef<HTMLSpanElement>(null);
  const needleRef = useRef<HTMLSpanElement>(null);
  const zoneRef = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    register(sensor.id, { value: valueRef.current, needle: needleRef.current, zone: zoneRef.current });
    return () => register(sensor.id, null);
  }, [sensor.id, register]);

  const shown = shownFor(sensor);
  const { low, high } = sensor.tolerance;
  const { min, width } = gaugeAxis(sensor);
  const pct = (v: number) => `${((v - min) / width) * 100}%`;
  const ext = sensor.tolerance_kind === "measuring_range" ? sensor.spec?.zones_um : undefined;
  const state = status?.state ?? "ok";
  const active = status?.active ?? [];
  const live = status?.live;
  const part = status?.part;
  const station = status?.station;

  return (
    <div className={`readout readout-${state}`}>
      <div className="readout-head">
        <span className="readout-name">{sensor.name}</span>
        <span className="readout-model">{sensor.model}</span>
      </div>
      <div className="readout-value">
        <span ref={valueRef}>…</span>
        <small>{shown.unit}</small>
      </div>
      <div className={`gauge ${ext ? "gauge-range" : ""}`} aria-hidden="true">
        {ext && <span className="gauge-ext" style={{ left: 0, width: pct(low) }} />}
        {ext && <span className="gauge-ext" style={{ left: pct(high), right: 0 }} />}
        <span className="gauge-go" style={{ left: pct(low), width: `${((high - low) / width) * 100}%` }} />
        <span className="gauge-tick" style={{ left: pct(low) }}><i>{tick(low, shown, "low")}</i></span>
        <span className="gauge-tick gauge-mid" style={{ left: pct((low + high) / 2) }} />
        <span className="gauge-tick" style={{ left: pct(high) }}><i>{tick(high, shown, "high")}</i></span>
        {ext && <span className="gauge-tick gauge-edge" style={{ left: "0%" }}><i>{tick(ext.ext_low, shown, "low")}</i></span>}
        {ext && <span className="gauge-tick gauge-edge" style={{ left: "100%" }}><i>{tick(ext.ext_high, shown, "high")}</i></span>}
        <span className="gauge-needle" ref={needleRef} />
      </div>
      <div className="readout-foot">
        {ext && <span className="readout-zone" ref={zoneRef} data-zone="standard">{ZONE_WORDS.standard}</span>}
        <span>noise {num(status?.noise ?? null, 2)} {sensor.unit}</span>
        {active.length > 0 && <span className="readout-active">{active.map((k) => KIND_LABEL[k]).join(", ")}</span>}
      </div>
      {live && <LiveLine live={live} />}
      {part?.supported && (
        <button className={`readout-part part-${part.state}`} onClick={() => controller.openPartCheck(sensor.id)}
          title="Teach a part and flag every other one">
          {PART_CHIP[part.state]}{part.state !== "no_reference" && !part.steady ? " …" : ""}
        </button>
      )}
      {station && (station.ready || station.parts > 0) && (
        <button className="readout-station" onClick={() => controller.openParts(sensor.id)}
          title="The quality station: every part, its height and verdict">
          <b key={station.parts} className="station-count">{station.parts}</b>
          <span>{station.parts === 1 ? "part" : "parts"}</span>
          <span className="station-good">{station.good} good</span>
          <span className={station.different ? "station-bad" : ""}>{station.different} different</span>
          {station.current && <span className="station-now">part {station.current.number} under the sensor</span>}
        </button>
      )}
    </div>
  );
}

const PART_CHIP: Record<NonNullable<SensorStatus["part"]>["state"], string> = {
  no_reference: "Part check: teach a part",
  waiting: "Part check: waiting for a part",
  match: "Taught part",
  different: "Different part",
  empty: "Nothing under the sensor",
};

export function SensorStrip() {
  const config = useStore((s) => s.config);
  const refs = useRef(new Map<string, Els>());

  useEffect(() => {
    let raf = 0;
    let last = 0;
    const tick = (now: number) => {
      if (now - last > 80) {
        last = now;
        for (const sensor of useStore.getState().config?.sensors ?? []) {
          const r = refs.current.get(sensor.id);
          const latest = series(sensor.id).latest();
          const v = latest && Number.isFinite(latest.v) ? latest.v : null;
          if (r?.value) r.value.textContent = v === null ? "no reading" : pos(v, shownFor(sensor), 2);
          if (r?.zone) {
            const zone = zoneOf(v, sensor);
            r.zone.textContent = zone ? ZONE_WORDS[zone] : "no reading";
            r.zone.dataset.zone = zone ?? "none";
          }
          if (r?.needle) {
            const { min, width } = gaugeAxis(sensor);
            const p = v === null ? 50 : Math.min(Math.max(((v - min) / width) * 100, 0), 100);
            r.needle.style.left = `${p}%`;
            r.needle.style.opacity = v === null ? "0.25" : "1";
          }
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  const register = useRef((id: string, els: Els | null) => {
    if (els) refs.current.set(id, els);
    else refs.current.delete(id);
  }).current;

  return (
    <div className="strip">
      {config?.sensors.map((sensor) => <Readout key={sensor.id} sensor={sensor} register={register} />)}
    </div>
  );
}
