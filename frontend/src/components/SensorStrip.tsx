// Live readouts under the twin: value, a go/no-go gauge, noise and setup hint.
import { useEffect, useRef } from "react";
import { controller } from "../controller";
import { KIND_LABEL, num, signed } from "../lib/format";
import { series } from "../lib/live";
import { useStore } from "../state/store";
import type { LiveStatus, SensorInfo } from "../types";

const SPAN = 1.3; // the gauge shows 1.3 times the tolerance band

function kilohertz(hz: number | null): string {
  if (!hz) return "";
  return hz >= 1000 ? `${(hz / 1000).toFixed(hz % 1000 === 0 ? 0 : 1)} kHz` : `${hz} Hz`;
}

/** Connection of a real sensor: what is live, or in plain words what is wrong. */
function LiveLine({ live }: { live: LiveStatus }) {
  const ok = live.state === "streaming";
  const parts = ok
    ? ["Live sensor", kilohertz(live.rate_hz), live.host,
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
  register: (id: string, value: HTMLElement | null, needle: HTMLElement | null) => void;
}) {
  const status = useStore((s) => s.status?.sensors[sensor.id]);
  const hint = useStore((s) => s.hints[sensor.id]);
  const valueRef = useRef<HTMLSpanElement>(null);
  const needleRef = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    register(sensor.id, valueRef.current, needleRef.current);
    return () => register(sensor.id, null, null);
  }, [sensor.id, register]);

  const { low, high } = sensor.tolerance;
  const width = (high - low) * SPAN;
  const min = (low + high) / 2 - width / 2;
  const pct = (v: number) => `${((v - min) / width) * 100}%`;
  const state = status?.state ?? "ok";
  const active = status?.active ?? [];
  const live = status?.live;

  return (
    <div className={`readout readout-${state}`}>
      <div className="readout-head">
        <span className="readout-name">{sensor.name}</span>
        <span className="readout-model">{sensor.model}</span>
      </div>
      <div className="readout-value">
        <span ref={valueRef}>…</span>
        <small>{sensor.unit}</small>
      </div>
      <div className="gauge" aria-hidden="true">
        <span className="gauge-go" style={{ left: pct(low), width: `${((high - low) / width) * 100}%` }} />
        <span className="gauge-tick" style={{ left: pct(low) }}><i>{num(low, 0)}</i></span>
        <span className="gauge-tick gauge-mid" style={{ left: pct((low + high) / 2) }} />
        <span className="gauge-tick" style={{ left: pct(high) }}><i>{signed(high, 0)}</i></span>
        <span className="gauge-needle" ref={needleRef} />
      </div>
      <div className="readout-foot">
        <span>noise {num(status?.noise ?? null, 2)} {sensor.unit}</span>
        {status?.settings?.averaging !== undefined && <span>averaging {String(status.settings.averaging)}</span>}
        {active.length > 0 && <span className="readout-active">{active.map((k) => KIND_LABEL[k]).join(", ")}</span>}
      </div>
      {live && <LiveLine live={live} />}
      {hint === "improve" && (
        <button className="readout-hint" onClick={() => controller.openAssistant(sensor.id)}>
          Setup can be better. Check it
        </button>
      )}
    </div>
  );
}

export function SensorStrip() {
  const config = useStore((s) => s.config);
  const refs = useRef(new Map<string, { value: HTMLElement | null; needle: HTMLElement | null }>());

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
          if (r?.value) r.value.textContent = v === null ? "no reading" : signed(v, 2);
          if (r?.needle) {
            const { low, high } = sensor.tolerance;
            const width = (high - low) * SPAN;
            const min = (low + high) / 2 - width / 2;
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

  const register = useRef((id: string, value: HTMLElement | null, needle: HTMLElement | null) => {
    if (value || needle) refs.current.set(id, { value, needle });
    else refs.current.delete(id);
  }).current;

  return (
    <div className="strip">
      {config?.sensors.map((sensor) => <Readout key={sensor.id} sensor={sensor} register={register} />)}
    </div>
  );
}
