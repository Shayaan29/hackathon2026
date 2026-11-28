// Mission mode overlay: drawn over the darkened twin for the pitch.
import { useEffect, useRef, useState } from "react";
import { KIND_LABEL } from "../lib/format";
import { pos, shownFor } from "../lib/units";
import { series } from "../lib/live";
import { useStore } from "../state/store";
import type { SensorInfo } from "../types";
import { Orb } from "./Copilot";

const R = 54;
const ARC = 0.75; // the dial covers three quarters of a circle
const CIRC = 2 * Math.PI * R;

function Dial({ sensor, side }: { sensor: SensorInfo; side: "left" | "right" }) {
  const state = useStore((s) => s.status?.sensors[sensor.id]?.state ?? "ok");
  const arcRef = useRef<SVGCircleElement>(null);
  const valueRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    let raf = 0;
    let last = 0;
    const { low, high } = sensor.tolerance;
    const tick = (now: number) => {
      if (now - last > 60) {
        last = now;
        const latest = series(sensor.id).latest();
        const v = latest && Number.isFinite(latest.v) ? latest.v : null;
        // 0 at the low limit, 1 at the high limit; the arc grows from the middle
        const p = v === null ? 0.5 : Math.min(Math.max((v - low) / (high - low), 0), 1);
        const from = Math.min(p, 0.5);
        const len = Math.max(Math.abs(p - 0.5), 0.004) * ARC * CIRC;
        if (arcRef.current) {
          arcRef.current.style.strokeDasharray = `${len} ${CIRC}`;
          arcRef.current.style.strokeDashoffset = `${-from * ARC * CIRC}`;
        }
        if (valueRef.current) valueRef.current.textContent = v === null ? "–" : pos(v, shownFor(sensor), 1);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [sensor]);

  return (
    <div className={`dial dial-${side} dial-${state}`}>
      <svg viewBox="0 0 140 140" aria-hidden="true">
        <g transform="rotate(135 70 70)">
          <circle cx="70" cy="70" r={R} className="dial-track" style={{ strokeDasharray: `${ARC * CIRC} ${CIRC}` }} />
          <circle cx="70" cy="70" r={R} className="dial-arc" ref={arcRef} />
        </g>
        {Array.from({ length: 31 }, (_, i) => {
          const a = ((135 + (270 * i) / 30) * Math.PI) / 180;
          const r1 = i % 5 === 0 ? 62 : 64;
          return <line key={i} x1={70 + r1 * Math.cos(a)} y1={70 + r1 * Math.sin(a)}
                       x2={70 + 67 * Math.cos(a)} y2={70 + 67 * Math.sin(a)} className="dial-tick" />;
        })}
      </svg>
      <div className="dial-text">
        <span ref={valueRef}>–</span>
        <small>{shownFor(sensor).unit}</small>
      </div>
      <div className="dial-name">{sensor.name}</div>
    </div>
  );
}

/** The quality station counter: every part that rested under the sensor, as it happens. */
function StationHud({ sensor }: { sensor: SensorInfo }) {
  const station = useStore((s) => s.status?.sensors[sensor.id]?.station);
  if (!station || (!station.ready && station.parts === 0)) return null;
  const shown = shownFor(sensor);
  const last = station.last;
  const height = last?.height_um ?? null;
  const scale = shown.display?.scale ?? 1;
  return (
    <div className="hud-station">
      <span className="hud-station-label">Parts</span>
      <span className="hud-station-count" key={station.parts}>{station.parts}</span>
      <span className="hud-station-split">
        <span className="is-good">{station.good} good</span>
        <span className={station.different ? "is-bad" : ""}>{station.different} different</span>
      </span>
      {last?.photo && <img className={`hud-station-photo is-${last.verdict}`} src={`${last.photo}?n=${last.number}`} alt={`Part ${last.number}`} key={`photo-${last.number}`} />}
      {last && (
        <span className={`hud-station-last is-${last.verdict}`} key={`last-${last.number}`}>
          Part {last.number}: {height !== null ? `${(height * scale).toFixed(shown.display ? 3 : 1)} ${shown.unit} high` : last.verdict}
        </span>
      )}
    </div>
  );
}

function useElapsed(since: number): string {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(id);
  }, []);
  const s = Math.max(0, Math.floor((now - since) / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

export function MissionHud() {
  const mission = useStore((s) => s.mission);
  const missionSince = useStore((s) => s.missionSince);
  const config = useStore((s) => s.config);
  const alert = useStore((s) => s.alert);
  const phones = useStore((s) => s.status?.clients.phone ?? 0);
  const feed = useStore((s) => s.feed);
  const elapsed = useElapsed(missionSince);
  if (!mission || !config) return null;

  const sensors = config.sensors;
  const half = Math.ceil(sensors.length / 2);
  // the copilot's latest words; while a fresh alert has no answer yet, say it is on it
  const lastLine = [...feed].reverse().find((f) => f.type === "say" && f.who !== "you");
  const spokenAfterAlert = lastLine && (!alert || lastLine.t >= alert.t);
  const line = spokenAfterAlert && lastLine.type === "say" ? lastLine.text
    : alert ? `${config.assistant_name} is on it.`
      : `${config.assistant_name} is watching ${sensors.length === 1 ? "the sensor" : `${sensors.length} sensors`}.`;

  return (
    <div className="hud" aria-hidden="true">
      {(["tl", "tr", "bl", "br"] as const).map((c) => (
        <svg key={c} className={`hud-corner hud-${c}`} viewBox="0 0 60 60"><path d="M2 58V2h56" /></svg>
      ))}
      <div className="hud-top-left">
        <span className="hud-title">{config.title}</span>
        <span className="hud-sub">Mission {elapsed}</span>
      </div>
      <div className="hud-top-right">
        <span className="hud-title">{phones} {phones === 1 ? "phone" : "phones"} connected</span>
        <span className="hud-sub">Scan the code to join: press Q</span>
      </div>
      <div className="hud-dials hud-dials-left">{sensors.slice(0, half).map((s) => <Dial key={s.id} sensor={s} side="left" />)}</div>
      <div className="hud-dials hud-dials-right">{sensors.slice(half).map((s) => <Dial key={s.id} sensor={s} side="right" />)}</div>
      {sensors[0] && <div className={`hud-station-wrap ${sensors.length > 1 ? "is-low" : ""}`}><StationHud sensor={sensors[0]} /></div>}
      {alert && (
        <div className={`hud-alert sev-${alert.severity}`} key={alert.id}>
          <span className="hud-alert-kind">{KIND_LABEL[alert.kind]} on {config.sensors.find((s) => s.id === alert.sensor)?.name}</span>
          <span className="hud-alert-text">{alert.summary}</span>
        </div>
      )}
      <div className="hud-voice">
        <Orb size={88} />
        <p>{line}</p>
      </div>
    </div>
  );
}
