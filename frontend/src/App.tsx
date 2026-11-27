import { lazy, Suspense, useEffect } from "react";
import { controller } from "./controller";
import { AssistantPanel } from "./components/AssistantPanel";
import { Copilot } from "./components/Copilot";
import { DemoPanel } from "./components/DemoPanel";
import { Header } from "./components/Header";
import { IncidentDock } from "./components/IncidentDock";
import { ManualCard } from "./components/ManualCard";
import { ManualSheet } from "./components/ManualSheet";
import { MissionHud } from "./components/MissionHud";
import { PartCheckPanel } from "./components/PartCheckPanel";
import { QrOverlay } from "./components/QrOverlay";
import { RecordingSheet } from "./components/RecordingSheet";
import { ReportModal } from "./components/ReportModal";
import { SensorStrip } from "./components/SensorStrip";
import { Stage } from "./components/Stage";
import { Toasts } from "./components/Toasts";
import { TwinView } from "./components/TwinView";
import { useStore } from "./state/store";

const GestureControl = lazy(() => import("./components/GestureControl"));

export default function App() {
  const mission = useStore((s) => s.mission);
  const demoOpen = useStore((s) => s.demoOpen);
  const qrOpen = useStore((s) => s.qrOpen);
  const gesturesOn = useStore((s) => s.gesturesOn);
  const gesture = useStore((s) => s.gesture);
  const hasChart = useStore((s) => s.stage.some((p) => !p.leaving));

  useEffect(() => {
    void controller.start();
  }, []);

  return (
    <div className={`app ${mission ? "is-mission" : ""} ${hasChart ? "has-chart" : ""}`}>
      <Header />
      <main className="cockpit">
        <section className="scene">
          <div className="scene-twin">
            <TwinView />
            <SensorStrip />
          </div>
          <div className="scene-stage">
            <Stage />
          </div>
        </section>
        <Copilot />
      </main>
      <MissionHud />
      {mission && (
        <button className="leave-mission" onClick={() => controller.toggleMission(false)}>Leave mission mode</button>
      )}
      {demoOpen && <DemoPanel />}
      {qrOpen && <QrOverlay />}
      <ReportModal />
      <AssistantPanel />
      <PartCheckPanel />
      <RecordingSheet />
      <ManualSheet />
      <IncidentDock />
      <ManualCard />
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
