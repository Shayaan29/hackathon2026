// Top bar: name, one health statement, and the few controls an operator needs.
import { useEffect, useState } from "react";
import { controller } from "../controller";
import { KIND_LABEL } from "../lib/format";
import { useStore } from "../state/store";
import { Icon } from "./Icon";

function useClock(): string {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** One sentence about the whole line, instead of a row of status pills. */
function Health() {
  const config = useStore((s) => s.config);
  const status = useStore((s) => s.status);
  const socket = useStore((s) => s.socket);
  if (socket !== "open") return <div className="health health-off"><span />Reconnecting to the sensors…</div>;
  const sensors = config?.sensors ?? [];
  const problems = sensors.flatMap((s) => {
    const st = status?.sensors[s.id];
    return st && st.state !== "ok" ? [{ name: s.name, state: st.state, kinds: st.active }] : [];
  });
  const fallback = sensors.some((s) => status?.sensors[s.id]?.source.fallback);
  if (!problems.length) {
    return (
      <div className="health health-ok"><span />
        All {sensors.length} sensors in tolerance{fallback ? " (simulated data)" : ""}
      </div>
    );
  }
  const worst = problems.some((p) => p.state === "critical") ? "critical" : "warning";
  const first = problems[0];
  const what = first.kinds.length ? KIND_LABEL[first.kinds[0]].toLowerCase() : "attention needed";
  return (
    <div className={`health health-${worst}`}><span />
      {first.name}: {what}{problems.length > 1 ? `, and ${problems.length - 1} more` : ""}
    </div>
  );
}

export function Header() {
  const config = useStore((s) => s.config);
  const mission = useStore((s) => s.mission);
  const soundOn = useStore((s) => s.soundOn);
  const gesturesOn = useStore((s) => s.gesturesOn);
  const phones = useStore((s) => s.status?.clients.phone ?? 0);
  const tokenOk = useStore((s) => s.tokenOk);
  const patch = useStore((s) => s.patch);
  const time = useClock();

  return (
    <header className="header">
      <div className="brand">
        <svg viewBox="0 0 32 32" className="brand-mark" aria-hidden="true">
          <path d="M5 26 16 6l11 20" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinejoin="round" />
          <path d="M16 6v14" stroke="var(--laser)" strokeWidth="2.4" strokeLinecap="round" />
          <circle cx="16" cy="22.5" r="2.6" fill="var(--laser)" />
        </svg>
        <div>
          <div className="brand-title">{config?.title ?? "KI Sensor Cockpit"}</div>
          <div className="brand-team">{config?.team ?? ""}</div>
        </div>
      </div>
      <Health />
      <nav className="header-actions">
        {config?.token_required && tokenOk === false && (
          <button className="tool tool-warn" onClick={() => void controller.enterToken()} title="Unlock demo controls">
            <Icon name="lock" /><span>Unlock</span>
          </button>
        )}
        <button className="tool" onClick={() => patch({ qrOpen: true })} title="Phones (Q)">
          <Icon name="phone" /><span>{phones ? `${phones} phone${phones === 1 ? "" : "s"}` : "Phones"}</span>
        </button>
        <button className={`tool ${gesturesOn ? "is-on" : ""}`} onClick={() => patch({ gesturesOn: !gesturesOn })} title="Hand gestures">
          <Icon name="hand" />
        </button>
        <button className={`tool ${soundOn ? "" : "is-off"}`} onClick={() => controller.toggleSound()} title="Sound (M)">
          <Icon name={soundOn ? "speaker" : "mute"} />
        </button>
        <button className="tool" onClick={() => patch({ demoOpen: !useStore.getState().demoOpen })} title="Demo controls (D)">
          <Icon name="play" />
        </button>
        <button className={`mission-btn ${mission ? "is-on" : ""}`} onClick={() => controller.toggleMission()} title="Mission mode (J)">
          <Icon name="mission" />
          <span>{mission ? "Leave mission mode" : "Mission mode"}</span>
        </button>
        <time className="header-clock">{time}</time>
      </nav>
    </header>
  );
}
