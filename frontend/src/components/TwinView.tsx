// The 3D digital twin with measurement callouts that follow each sensor.
import { useEffect, useRef, useState } from "react";
import { signed } from "../lib/format";
import { series } from "../lib/live";
import { serverNow } from "../lib/live";
import { trackFor } from "../lib/tracks";
import type { Twin, TwinState } from "../lib/twin";
import { useStore } from "../state/store";

const get = () => useStore.getState();

function sensorState(id: string): TwinState {
  const status = get().status?.sensors[id];
  if (!status) return "ok";
  if (status.active.includes("invalid")) return "invalid";
  return status.state;
}

export function TwinView() {
  const config = useStore((s) => s.config);
  const hostRef = useRef<HTMLDivElement>(null);
  const labelRefs = useRef(new Map<string, HTMLDivElement>());
  const valueRefs = useRef(new Map<string, HTMLSpanElement>());
  const twinRef = useRef<Twin | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [shake, setShake] = useState<{ key: number; name: string } | null>(null);
  const sensorKey = config?.sensors.map((s) => s.id).join(",") ?? "";

  useEffect(() => {
    const host = hostRef.current;
    if (!host || !config) return;
    let disposed = false;
    let raf = 0;
    let lastText = 0;
    void import("../lib/twin").then(({ Twin }) => {
      if (disposed) return;
      try {
        const twin = new Twin(host, config.sensors.map((s, i) => ({
          id: s.id, range: Math.max((s.tolerance.high - s.tolerance.low) / 2, 1e-6), nominal: s.nominal,
          ...trackFor(i, config.sensors.length),
        })), {
          forEachSince: (id, tMin, fn) => series(id).forEachSince(tMin, fn),
          now: serverNow,
          value: (id) => {
            const latest = series(id).latest();
            const nominal = get().config?.sensors.find((s) => s.id === id)?.nominal ?? 0;
            return latest && Number.isFinite(latest.v) ? latest.v - nominal : null;
          },
          state: sensorState,
        }, (2 * Math.PI * (config.part_rpm ?? 8.6)) / 60);
        twinRef.current = twin;
        twin.setMission(get().mission);
      } catch (error) {
        setFailed(error instanceof Error ? error.message : String(error));
        return;
      }
      // callouts follow the sensors; values refresh about ten times a second
      const tick = (now: number) => {
        const twin = twinRef.current;
        if (twin) {
          for (const p of twin.labelPositions()) {
            const el = labelRefs.current.get(p.id);
            if (el) {
              el.style.transform = `translate(${p.x.toFixed(1)}px, ${p.y.toFixed(1)}px)`;
              el.style.opacity = p.visible ? "1" : "0";
              el.dataset.state = sensorState(p.id);
            }
          }
          if (now - lastText > 100) {
            lastText = now;
            for (const sensor of get().config?.sensors ?? []) {
              const el = valueRefs.current.get(sensor.id);
              const latest = series(sensor.id).latest();
              if (el) el.textContent = latest && Number.isFinite(latest.v) ? signed(latest.v, 2) : "no reading";
            }
          }
        }
        raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
    });
    return () => {
      disposed = true;
      cancelAnimationFrame(raf);
      twinRef.current?.dispose();
      twinRef.current = null;
    };
    // the scene is rebuilt only when the set of sensors changes
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sensorKey]);

  // mission mode, knocks from vibration alerts and judges' shakes
  useEffect(() => {
    let lastEvents = get().events.length;
    let lastShakeT = get().lastShake?.t ?? 0;
    return useStore.subscribe((s, prev) => {
      if (s.mission !== prev.mission) twinRef.current?.setMission(s.mission);
      if (s.events.length > lastEvents) {
        for (const event of s.events.slice(lastEvents)) {
          if (event.kind === "vibration") {
            const peak = Number(event.numbers.peak ?? 15);
            twinRef.current?.knock(Math.min(Math.max(peak / 15, 0.6), 2.5));
          }
        }
      }
      lastEvents = s.events.length;
      if (s.lastShake && s.lastShake.t !== lastShakeT) {
        lastShakeT = s.lastShake.t;
        twinRef.current?.knock(1 + s.lastShake.intensity * 0.6);
        setShake({ key: s.lastShake.t, name: s.lastShake.name });
      }
    });
  }, []);

  return (
    <div className="twin">
      <div className="twin-canvas" ref={hostRef} />
      {failed && (
        <div className="twin-failed">
          <p>The 3D view needs WebGL, which this browser has switched off.</p>
          <p className="muted">{failed}</p>
        </div>
      )}
      {!failed && config?.sensors.map((sensor, i) => (
        <div key={sensor.id} className="callout" data-state="ok"
             data-side={i < config.sensors.length / 2 && config.sensors.length > 1 ? "left" : "right"}
             ref={(el) => { if (el) labelRefs.current.set(sensor.id, el); else labelRefs.current.delete(sensor.id); }}>
          <span className="callout-dot" />
          <span className="callout-leader" />
          <span className="callout-body">
            <span className="callout-name">{sensor.name}</span>
            <span className="callout-value">
              <span ref={(el) => { if (el) valueRefs.current.set(sensor.id, el); else valueRefs.current.delete(sensor.id); }}>…</span>
              <small>{sensor.unit}</small>
            </span>
          </span>
        </div>
      ))}
      {shake && (
        <div className="shake-note" key={shake.key} onAnimationEnd={() => setShake(null)}>
          <strong>{shake.name}</strong> knocked the machine
        </div>
      )}
      <div className="twin-hint">Drag to look around</div>
    </div>
  );
}
