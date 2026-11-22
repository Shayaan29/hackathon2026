import { controller } from "../controller";
import { useStore } from "../state/store";
import { Icon } from "./Icon";

const ACTIONS: { key: string; label: string; kind: string; body: Record<string, unknown>; hint: string }[] = [
  { key: "1", label: "Table tap", kind: "tap", body: { sensor: "s1" }, hint: "30 Hz vibration on the fixture" },
  { key: "2", label: "Start drift", kind: "drift", body: { sensor: "s2", rate_per_min: 6 }, hint: "Sensor 2, +6 µm/min" },
  { key: "3", label: "Stop drift", kind: "drift_stop", body: { sensor: "s2" }, hint: "holds the offset" },
  { key: "4", label: "Noise burst", kind: "noise", body: { sensor: "s1", factor: 4, duration_s: 6 }, hint: "Sensor 1, 6 s" },
  { key: "5", label: "Dropout", kind: "dropout", body: { sensor: "s1", share: 0.5, duration_s: 3 }, hint: "invalid readings" },
  { key: "6", label: "Misconfigure", kind: "misconfigure", body: { sensor: "s1" }, hint: "Sensor 1 averaging set to 2" },
  { key: "0", label: "Reset", kind: "reset", body: {}, hint: "back to normal, settings too" },
];

export function DemoPanel() {
  const patch = useStore((s) => s.patch);
  const tokenOk = useStore((s) => s.tokenOk);
  const config = useStore((s) => s.config);
  const simulated = useStore((s) => Object.values(s.status?.sensors ?? {}).every((x) => x.source.type === "simulator"));

  return (
    <aside className="demo">
      <header className="section-head">
        <span>Demo controls</span>
        <button className="icon-btn" onClick={() => patch({ demoOpen: false })} title="Close (D)">
          <Icon name="close" />
        </button>
      </header>
      {!simulated && <p className="demo-note">Demo events only work on simulated sensors.</p>}
      {config?.token_required && tokenOk === false && (
        <button className="btn btn-warn wide" onClick={() => void controller.enterToken()}>
          <Icon name="lock" /> Enter cockpit token
        </button>
      )}
      <div className="demo-grid">
        {ACTIONS.map((a) => (
          <button key={a.kind} className="demo-btn" onClick={() => void controller.demo(a.kind, a.body)}>
            <span className="demo-key">{a.key}</span>
            <span className="demo-label">{a.label}</span>
            <span className="demo-hint">{a.hint}</span>
          </button>
        ))}
      </div>
      <div className="demo-row">
        <button className="btn" onClick={() => controller.say("Voice check. The cockpit can talk.", "en")}>Test speech</button>
        <button className="btn" onClick={() => void controller.submitText("status")}>Status report</button>
      </div>
      <p className="demo-note">
        Keys while this panel is open: 1 to 6 and 0. Anywhere: Space to talk, J for mission mode, R for the
        latest report, ← back, Delete to dismiss a chart, Q for the phone code, M for sound.
      </p>
    </aside>
  );
}
