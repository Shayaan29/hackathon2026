// The judges' phone page: tap to join, then the phone flashes, beeps and
// vibrates the moment the cockpit raises an alert.
import { useEffect, useRef, useState } from "react";
import { KIND_LABEL, ago } from "../lib/format";
import { LiveSocket, type SocketState } from "../lib/socket";
import { alarm, chime, unlockAudio } from "../lib/sound";
import type { CockpitEvent, PartRecord, ServerMessage, StationState } from "../types";
import { Torch } from "./torch";

interface PhoneHello {
  type: "hello";
  title: string;
  team: string;
  sensors: { id: string; name: string }[];
  events: CockpitEvent[];
}

interface WakeLockLike {
  release(): Promise<void>;
}

async function keepAwake(): Promise<WakeLockLike | null> {
  try {
    const nav = navigator as Navigator & { wakeLock?: { request(type: "screen"): Promise<WakeLockLike> } };
    return (await nav.wakeLock?.request("screen")) ?? null;
  } catch {
    return null;
  }
}

export function PhoneApp() {
  const [joined, setJoined] = useState(false);
  const [connection, setConnection] = useState<SocketState>("connecting");
  const [hello, setHello] = useState<PhoneHello | null>(null);
  const [last, setLast] = useState<CockpitEvent | null>(null);
  const [flash, setFlash] = useState<CockpitEvent | null>(null);
  const [station, setStation] = useState<StationState | null>(null);
  const [parts, setParts] = useState<PartRecord[]>([]);
  const [, tick] = useState(0);
  const socket = useRef<LiveSocket | null>(null);
  const wake = useRef<WakeLockLike | null>(null);
  const flashTimer = useRef(0);
  const torch = useRef(new Torch());
  const [useLight, setUseLight] = useState(true);
  const [light, setLight] = useState<"off" | "on" | "unsupported">("off");
  const [name, setName] = useState(() => {
    try { return localStorage.getItem("phone-name") ?? ""; } catch { return ""; }
  });
  useEffect(() => {
    const id = window.setInterval(() => tick((n) => n + 1), 5000);
    const onVisible = async () => {
      if (document.visibilityState === "visible" && joined) wake.current = await keepAwake();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [joined]);

  useEffect(() => () => {
    socket.current?.stop();
    torch.current.stop();
  }, []);

  const onMessage = (message: ServerMessage | PhoneHello) => {
    if (message.type === "hello" && "sensors" in message && !("config" in message)) {
      setHello(message);
      const events = message.events;
      if (events.length) setLast(events[events.length - 1]);
      const first = message.sensors[0]?.id;
      if (first) {
        fetch(`/api/parts/${encodeURIComponent(first)}?limit=6`)
          .then((r) => (r.ok ? r.json() : null))
          .then((d: (StationState & { records?: PartRecord[] }) | null) => {
            if (!d) return;
            setStation(d);
            setParts(d.records ?? []);
          })
          .catch(() => { /* the list fills with the next part */ });
      }
      return;
    }
    if (message.type === "part") {
      setStation(message.station);
      const part = message.part;
      if (!part) setParts([]);
      else setParts((list) => [part, ...list.filter((p) => p.number !== part.number)].slice(0, 6));
      return;
    }
    if (message.type === "event") {
      const event = message as CockpitEvent;
      setLast(event);
      setFlash(event);
      if (event.severity === "critical") alarm();
      else chime();
      const critical = event.severity === "critical";
      navigator.vibrate?.(critical ? [300, 120, 300, 120, 300, 120, 300] : [220, 100, 220, 100, 220]);
      void (critical ? torch.current.blink(6, 160, 120) : torch.current.blink(3, 220, 180));
      window.clearTimeout(flashTimer.current);
      flashTimer.current = window.setTimeout(() => setFlash(null), 7000);
    }
  };

  const join = async () => {
    try { localStorage.setItem("phone-name", name.trim()); } catch { /* private mode */ }
    unlockAudio();
    navigator.vibrate?.(40);
    wake.current = await keepAwake();
    if (useLight) setLight((await torch.current.enable()) ? "on" : "unsupported");
    setJoined(true);
    socket.current = new LiveSocket("/ws/phone", (m) => onMessage(m as ServerMessage), setConnection);
    socket.current.start();
  };

  if (!joined) {
    return (
      <main className="ph ph-join">
        <div className="ph-logo">
          <svg viewBox="0 0 64 64" aria-hidden="true">
            <circle cx="32" cy="32" r="17" fill="none" stroke="currentColor" strokeWidth="4" />
            <circle cx="32" cy="32" r="6" fill="currentColor" />
            <path d="M6 32h9M49 32h9" stroke="currentColor" strokeWidth="4" strokeLinecap="round" />
          </svg>
        </div>
        <h1>Live sensor alerts</h1>
        <p>Your phone will flash and sound the moment a sensor needs attention, just like a maintenance technician's would on the shop floor.</p>
        <input className="ph-name" value={name} maxLength={24} onChange={(e) => setName(e.target.value)}
               placeholder="Your name (optional)" aria-label="Your name" autoComplete="given-name" />
        <button className="ph-join-btn" onClick={() => void join()}>Join live alerts</button>
        <label className="ph-light-opt">
          <input type="checkbox" checked={useLight} onChange={(e) => setUseLight(e.target.checked)} />
          Also blink the flashlight (Android)
        </label>
        <p className="ph-small">Keep this page open. Turn the sound up.</p>
      </main>
    );
  }

  return (
    <main className={`ph ph-watch ${flash ? `ph-flash-${flash.severity}` : ""}`}>
      <header className="ph-head">
        <div>
          <div className="ph-title">{hello?.title ?? "KI Sensor Cockpit"}</div>
          <div className="ph-team">{hello?.team ?? ""}</div>
        </div>
        <div className="ph-badges">
          {light === "on" && <span className="ph-conn ph-light-on" title="The flashlight blinks on every alert">light</span>}
          <span className={`ph-conn ph-conn-${connection}`}>{connection === "open" ? "live" : "reconnecting"}</span>
        </div>
      </header>

      <section className="ph-watching">
        <div className="ph-radar" />
        <div className="ph-watch-text">
          Watching {hello?.sensors.length ?? 0} sensor{(hello?.sensors.length ?? 0) === 1 ? "" : "s"}
          <span>{hello?.sensors.map((s) => s.name).join(", ")}</span>
        </div>
      </section>

      {last ? (
        <section className={`ph-card ph-card-${last.severity}`}>
          <div className="ph-card-top">
            <span>{KIND_LABEL[last.kind]}</span>
            <span>{ago(last.t)}</span>
          </div>
          <div className="ph-card-text">{last.summary}</div>
        </section>
      ) : (
        <section className="ph-card ph-card-quiet">All quiet. Waiting for the next alert…</section>
      )}

      {station && (station.ready || station.parts > 0) && (
        <section className="ph-parts">
          <div className="ph-parts-top">
            <span className="ph-parts-count" key={station.parts}>{station.parts}</span>
            <span>parts<small>{station.good} good, {station.different} different</small></span>
          </div>
          {parts.length > 0 && (
            <ol className="ph-parts-list">
              {parts.map((p) => (
                <li key={p.number} className={`is-${p.verdict}`}>
                  <a href={`/part/${encodeURIComponent(p.sensor)}/${p.number}`} target="_blank" rel="noreferrer">
                    <span>Part {p.number}{p.photo ? " 📷" : ""}</span>
                    <span>{p.height ?? "–"}</span>
                    <span>{p.verdict === "good" ? "taught part" : "different"}</span>
                  </a>
                </li>
              ))}
            </ol>
          )}
        </section>
      )}

      {flash && (
        <div key={flash.id} role="alert" className={`ph-alert ph-alert-${flash.severity}`} onClick={() => setFlash(null)}>
          <svg className="ph-alert-icon" viewBox="0 0 64 64" aria-hidden="true">
            <path d="M32 6 L60 56 H4 Z" fill="none" stroke="currentColor" strokeWidth="5" strokeLinejoin="round" />
            <path d="M32 24 V38" stroke="currentColor" strokeWidth="6" strokeLinecap="round" />
            <circle cx="32" cy="47" r="3.6" fill="currentColor" />
          </svg>
          <span className="ph-alert-sensor">
            {hello?.sensors.find((s) => s.id === flash.sensor)?.name ?? flash.sensor}
          </span>
          <span className="ph-alert-kind">
            {flash.severity === "critical" ? "Critical: " : ""}{KIND_LABEL[flash.kind]}
          </span>
          <span className="ph-alert-text">{flash.summary}</span>
          <a className="ph-alert-report" href={`/report/${encodeURIComponent(flash.id)}`} target="_blank" rel="noreferrer"
             onClick={(e) => e.stopPropagation()}>Open the report</a>
          <span className="ph-alert-hint">Tap to close</span>
        </div>
      )}
    </main>
  );
}
