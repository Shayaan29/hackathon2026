import { useEffect } from "react";
import { controller } from "./controller";
import { Copilot } from "./components/Copilot";
import { Header } from "./components/Header";
import { ManualCard } from "./components/ManualCard";
import { ManualSheet } from "./components/ManualSheet";
import { MissionHud } from "./components/MissionHud";
import { PartCheckPanel } from "./components/PartCheckPanel";
import { PartsPanel } from "./components/PartsPanel";
import { CameraPreview } from "./components/CameraPreview";
import { QrOverlay } from "./components/QrOverlay";
import { ReportModal } from "./components/ReportModal";
import { SensorStrip } from "./components/SensorStrip";
import { Stage } from "./components/Stage";
import { Toasts } from "./components/Toasts";
import { TwinView } from "./components/TwinView";
import { useStore } from "./state/store";

export default function App() {
  const mission = useStore((s) => s.mission);
  const qrOpen = useStore((s) => s.qrOpen);
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
      {qrOpen && <QrOverlay />}
      <ReportModal />
      <PartCheckPanel />
      <PartsPanel />
      <CameraPreview />
      <ManualSheet />
      <ManualCard />
      <Toasts />
    </div>
  );
}
