import { useCallback, useEffect, useRef } from "react";
import { controller } from "../controller";
import { dissolve } from "../lib/particles";
import { useStore } from "../state/store";
import { AlertBanner } from "./AlertBanner";
import { ChartPanel } from "./ChartPanel";

export function Stage() {
  const stage = useStore((s) => s.stage);
  const alert = useStore((s) => s.alert);
  const stageRef = useRef<HTMLDivElement>(null);
  const dustRef = useRef<HTMLCanvasElement>(null);
  const panels = useRef(new Map<string, HTMLElement>());

  const register = useCallback((id: string, el: HTMLElement | null) => {
    if (el) panels.current.set(id, el);
    else panels.current.delete(id);
  }, []);

  useEffect(() => {
    controller.registerStage({
      dissolveFront: () => {
        const front = useStore.getState().stage.find((p) => !p.leaving);
        const el = front ? panels.current.get(front.id) : undefined;
        const canvas = el?.querySelector("canvas");
        if (!canvas || !dustRef.current || !stageRef.current) return false;
        void dissolve(canvas, canvas.getBoundingClientRect(), dustRef.current, stageRef.current.getBoundingClientRect());
        return true;
      },
    });
    return () => controller.registerStage(null);
  }, []);

  let depth = 0;
  const critical = alert?.severity === "critical";

  return (
    <section className={`stage ${alert ? (critical ? "stage-critical" : "stage-warning") : ""}`} ref={stageRef}>
      <div className="stage-floor" />
      {stage.map((panel) => {
        const d = panel.leaving ? 0 : depth++;
        return <ChartPanel key={panel.id} panel={panel} depth={d} register={register} />;
      })}
      {stage.length === 0 && (
        <div className="stage-empty">
          <div className="stage-empty-title">Ask for a chart</div>
          <div>"Show me the last five minutes" · hold Space to talk</div>
        </div>
      )}
      <canvas className="dust" ref={dustRef} />
      <AlertBanner />
      <div className="stage-hint">← back · Del dismiss · Space talk</div>
    </section>
  );
}
