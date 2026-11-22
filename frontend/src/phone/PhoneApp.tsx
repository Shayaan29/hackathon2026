// The judges' phone page: tap to join, then the phone flashes, beeps and
// vibrates the moment the cockpit raises an alert.
import { useEffect, useRef, useState } from "react";
import { KIND_LABEL, ago } from "../lib/format";
import { LiveSocket, type SocketState } from "../lib/socket";
import { alarm, chime, unlockAudio } from "../lib/sound";
import type { CockpitEvent, ServerMessage } from "../types";
import { Torch } from "./torch";
import { ShakeDetector, type ShakeSupport } from "./shake";

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
  const [shakeSupport, setShakeSupport] = useState<ShakeSupport>("unknown");
  const [shakeNote, setShakeNote] = useState<string | null>(null);
  const [crowd, setCrowd] = useState<string | null>(null);
  const noteTimer = useRef(0);
  const sendShake = useRef<(intensity: number) => void>(() => undefined);
  sendShake.current = (intensity: number) => {
    const sent = socket.current?.send(JSON.stringify({ type: "shake", name: name.trim(), intensity }));
    navigator.vibrate?.(60);
    note(sent ? "Sending your shake to the machine…" : "Not connected yet. Try again in a moment.");
  };
  const detector = useRef(new ShakeDetector((intensity) => sendShake.current(intensity)));
  const note = (text: string, ms = 4000) => {
    setShakeNote(text);
    window.clearTimeout(noteTimer.current);
    noteTimer.current = window.setTimeout(() => setShakeNote(null), ms);
  };

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
    detector.current.disable();
    socket.current?.stop();
    torch.current.stop();
  }, []);

  const onMessage = (message: ServerMessage | PhoneHello) => {
    if (message.type === "hello" && "sensors" in message && !("config" in message)) {
      setHello(message);
      const events = message.events;
      if (events.length) setLast(events[events.length - 1]);
      return;
    }
    const m = message as unknown as Record<string, unknown>;
    if (m.type === "shake_ack") {
      if (m.ok) note("It hit the machine. Watch the big screen.", 5000);
      else if (m.reason === "busy") note(`The machine is still ringing. Try again in ${Math.ceil(Number(m.retry_s ?? 3))} s.`);
      else note("Shaking only works while the cockpit runs on simulated sensors.", 6000);
      return;
    }
    if (m.type === "shake") {
      setCrowd(`${String(m.name)} knocked the machine`);
      window.setTimeout(() => setCrowd(null), 4000);
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
          Watching {hello?.sensors.length ?? 0} sensors
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

      <section className="ph-shake">
        {shakeSupport === "ready" ? (
          <>
            <div className="ph-shake-title">Shake your phone hard</div>
            <p>Three firm shakes knock the machine. The sensors will feel it, and the copilot will tell you what it measured.</p>
          </>
        ) : (
          <>
            <div className="ph-shake-title">Test the machine yourself</div>
            <p>Shake your phone to knock the measuring setup and see whether the AI notices.</p>
            <button className="ph-shake-btn" onClick={async () => {
              const support = await detector.current.enable();
              setShakeSupport(support);
              if (support === "denied") note("Motion access was refused. Use Knock instead.", 6000);
              if (support === "unsupported") note("This phone has no motion sensor here. Use Knock instead.", 6000);
            }}>Shake the machine</button>
          </>
        )}
        <button className="ph-knock" onClick={() => sendShake.current(1)}>Knock instead</button>
        {shakeNote && <p className="ph-shake-note">{shakeNote}</p>}
      </section>
      {crowd && <div className="ph-crowd">{crowd}</div>}

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
