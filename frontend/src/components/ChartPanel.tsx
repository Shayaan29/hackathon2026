import { useEffect, useRef, useState, type CSSProperties } from "react";
import { loadView } from "../lib/api";
import { driftOption, echarts, historyOption, spectrumOption, type ChartOption } from "../lib/charts";
import { ago, signed } from "../lib/format";
import type { StagePanel } from "../state/store";
import type { DriftData, HistoryData, SpectrumData, ViewData } from "../types";

const OPACITY = [1, 0.5, 0.26, 0.12, 0.05];

function placement(depth: number, entered: boolean, leaving?: StagePanel["leaving"]): CSSProperties {
  if (leaving === "forward") return { transform: "translate(-50%, -50%) translateZ(340px)", opacity: 0, zIndex: 40 };
  if (leaving === "dust") return { transform: "translate(-50%, -50%)", opacity: 0, transition: "none", zIndex: 40 };
  if (!entered) return { transform: "translate(-50%, -50%) translateZ(280px)", opacity: 0, zIndex: 30 };
  if (depth === 0) return { transform: "translate(-50%, -50%) translateZ(0px)", opacity: 1, zIndex: 20 };
  return {
    transform: `translate(-50%, calc(-50% - ${depth * 58}px)) translateZ(${-depth * 260}px)`,
    opacity: OPACITY[depth] ?? 0,
    zIndex: 20 - depth,
    pointerEvents: "none",
  };
}

function buildOption(data: ViewData): ChartOption {
  if (data.view === "spectrum") return spectrumOption(data);
  if (data.view === "drift") return driftOption(data);
  return historyOption(data);
}

function windowText(seconds: number): string {
  return seconds >= 120 ? `last ${Math.round(seconds / 60)} min` : `last ${Math.round(seconds)} s`;
}

function describe(data: ViewData | null, fallbackTitle: string): { title: string; detail: string; tone?: "warn" | "bad" } {
  if (!data) return { title: fallbackTitle, detail: "loading…" };
  if (data.view === "spectrum") {
    const d = data as SpectrumData;
    const when = d.event ? `vibration ${ago(d.event.t)}` : "live window";
    const peak = d.peak && d.peak.prominence >= 6
      ? `clear peak at ${d.peak.frequency_hz.toFixed(0)} Hz`
      : "no dominant frequency";
    return { title: `${d.name} · frequency spectrum`, detail: `${peak} · ${when}`, tone: d.peak && d.peak.prominence >= 6 ? "warn" : undefined };
  }
  if (data.view === "drift") {
    const d = data as DriftData;
    if (!d.fit || Math.abs(d.fit.rate_per_min) < 1) return { title: `${d.name} · drift and forecast`, detail: "no steady drift" };
    const minutes = d.forecast?.minutes_to_limit;
    const eta = minutes !== null && minutes !== undefined ? ` · tolerance limit in ~${Math.round(minutes)} min` : "";
    return {
      title: `${d.name} · drift and forecast`,
      detail: `${signed(d.fit.rate_per_min, 1)} ${d.unit}/min${eta}`,
      tone: minutes !== null && minutes !== undefined && minutes < 10 ? "bad" : "warn",
    };
  }
  const h = data as HistoryData;
  if (h.view === "compare") {
    return { title: h.series.map((s) => s.name).join(" vs "), detail: `${windowText(h.window_s)} · same time axis` };
  }
  const s = h.series[0];
  const last = [...(s?.values ?? [])].reverse().find((v) => v !== null);
  return { title: `${s?.name ?? ""} · live`, detail: `${windowText(h.window_s)} · now ${signed(last ?? null, 2)} ${s?.unit ?? ""}` };
}

export function ChartPanel({ panel, depth, register }: {
  panel: StagePanel;
  depth: number;
  register: (id: string, el: HTMLElement | null) => void;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<HTMLDivElement>(null);
  const instRef = useRef<echarts.ECharts | null>(null);
  const firstDraw = useRef(true);
  const [data, setData] = useState<ViewData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [entered, setEntered] = useState(false);

  useEffect(() => {
    register(panel.id, rootRef.current);
    let raf = requestAnimationFrame(() => {
      raf = requestAnimationFrame(() => setEntered(true));
    });
    return () => {
      cancelAnimationFrame(raf);
      register(panel.id, null);
    };
  }, [panel.id, register]);

  useEffect(() => {
    if (!chartRef.current) return;
    const inst = echarts.init(chartRef.current, undefined, { renderer: "canvas" });
    instRef.current = inst;
    const observer = new ResizeObserver(() => inst.resize());
    observer.observe(chartRef.current);
    return () => {
      observer.disconnect();
      inst.dispose();
      instRef.current = null;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer = 0;
    const view = panel.spec.view;
    const refreshMs = view === "drift" ? 4000 : view === "spectrum" ? 0 : 1000;
    const load = async () => {
      try {
        const result = await loadView(panel.spec);
        if (!cancelled) {
          setData(result);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
      if (!cancelled && refreshMs && depth === 0 && !panel.leaving) timer = window.setTimeout(load, refreshMs);
    };
    void load();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [panel.spec, depth, panel.leaving]);

  useEffect(() => {
    const inst = instRef.current;
    if (!inst || !data) return;
    const option = buildOption(data);
    if (firstDraw.current) {
      inst.setOption(option, { notMerge: true });
      firstDraw.current = false;
    } else {
      inst.setOption({ ...option, animation: false }, { notMerge: true });
    }
  }, [data]);

  const info = describe(data, panel.spec.view);

  return (
    <div ref={rootRef} className={`panel ${depth === 0 && !panel.leaving ? "panel-front" : ""}`}
         style={placement(depth, entered, panel.leaving)}>
      <span className="corner tl" />
      <span className="corner tr" />
      <span className="corner bl" />
      <span className="corner br" />
      <header className="panel-head">
        <div className="panel-title">{info.title}</div>
        <div className={`panel-detail ${info.tone ?? ""}`}>{error ? `error: ${error}` : info.detail}</div>
      </header>
      <div className="panel-chart" ref={chartRef} />
    </div>
  );
}
