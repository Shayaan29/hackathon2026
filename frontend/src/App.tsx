import { lazy, Suspense, useEffect } from "react";
import { controller } from "./controller";
import { DemoPanel } from "./components/DemoPanel";
import { EventLog } from "./components/EventLog";
import { QrOverlay } from "./components/QrOverlay";
import { SensorTile } from "./components/SensorTile";
import { Stage } from "./components/Stage";
import { Toasts } from "./components/Toasts";
import { TopBar } from "./components/TopBar";
import { VoiceDock } from "./components/VoiceDock";
import { useStore } from "./state/store";

const GestureControl = lazy(() => import("./components/GestureControl"));

export default function App() {
  const config = useStore((s) => s.config);
  const status = useStore((s) => s.status);
  const demoOpen = useStore((s) => s.demoOpen);
  const qrOpen = useStore((s) => s.qrOpen);
  const gesturesOn = useStore((s) => s.gesturesOn);
  const gesture = useStore((s) => s.gesture);

  useEffect(() => {
    void controller.start();
  }, []);

  return (
    <div className="app">
      <TopBar />
      <main className="layout">
        <aside className="sensors">
          <header className="section-head">
            <span>Sensors</span>
            <span className="muted">{config?.sensors.length ?? 0}</span>
          </header>
          {config?.sensors.map((sensor) => (
            <SensorTile key={sensor.id} sensor={sensor} status={status?.sensors[sensor.id]} />
          ))}
        </aside>
        <div className="center">
          <Stage />
          <VoiceDock />
        </div>
        <aside className="side">
          <EventLog />
        </aside>
      </main>
      {demoOpen && <DemoPanel />}
      {qrOpen && <QrOverlay />}
      {gesturesOn && (
        <Suspense fallback={null}>
          <GestureControl />
        </Suspense>
      )}
      {gesture && <div className="gesture-flash">{gesture.replace("_", " ")}</div>}
      <Toasts />
    </div>
  );
}
