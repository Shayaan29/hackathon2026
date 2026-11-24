import { useEffect, useRef, useState, type CSSProperties } from "react";
import { controller } from "../controller";
import { loadView } from "../lib/api";
import {
  barOption, boxplotOption, driftOption, echarts, histogramOption, historyOption, incidentOption, scatterOption,
  spectrumOption, withAnnotations, type ChartOption,
} from "../lib/charts";
import { ago, signed } from "../lib/format";
import { useStore, type StagePanel } from "../state/store";
import type {
  BarData, BoxplotData, DriftData, HistogramData, HistoryData, IncidentChartData, ScatterData, SpectrumData, ViewData,
} from "../types";
import { ShiftChart } from "./ShiftChart";

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
  switch (data.view) {
    case "spectrum": return spectrumOption(data);
    case "drift": return driftOption(data);
    case "histogram": return histogramOption(data);
    case "boxplot": return boxplotOption(data);
    case "scatter": return scatterOption(data);
    case "bar": return barOption(data);
    case "shift": return {};
    case "incident": return incidentOption(data as IncidentChartData);
    default: return historyOption(data as HistoryData);
  }
}

const GROUP_WORDS: Record<string, string> = { minute: "per minute", "10min": "per 10 minutes", hour: "per hour", sensor: "per sensor" };

function windowText(seconds: number): string {
  if (seconds >= 5400) return `Last ${Math.round(seconds / 3600)} hours`;
  return seconds >= 120 ? `Last ${Math.round(seconds / 60)} minutes` : `Last ${Math.round(seconds)} seconds`;
}

function describe(data: ViewData | null, fallbackTitle: string): { title: string; detail: string; tone?: "warn" | "bad" } {
  if (!data) return { title: fallbackTitle === "shift" ? "The whole shift" : fallbackTitle, detail: "loading…" };
  if (data.view === "histogram") {
    const d = data as HistogramData;
    const s = d.series[0];
    const names = d.series.map((x) => x.name).join(" and ");
    if (!s) return { title: "How the readings are spread", detail: "not enough data" };
    return { title: `${names}: how the readings are spread`,
      detail: `${windowText(d.window_s)}, Cpk ${s.cpk.toFixed(1)}`, tone: s.cpk < 1.33 ? "warn" : undefined };
  }
  if (data.view === "boxplot") {
    const d = data as BoxplotData;
    return { title: d.group_by === "sensor" ? "Spread per sensor" : `Spread ${GROUP_WORDS[d.group_by] ?? ""}`,
      detail: `${windowText(d.window_s)}, from ${d.per}` };
  }
  if (data.view === "scatter") {
    const d = data as ScatterData;
    return { title: `${d.x.name} against ${d.y.name}`, detail: `${windowText(d.window_s)}, correlation ${d.r.toFixed(2)}`,
      tone: Math.abs(d.r) >= 0.6 ? "warn" : undefined };
  }
  if (data.view === "bar") {
    const d = data as BarData;
    return { title: `${d.label.charAt(0).toUpperCase()}${d.label.slice(1)} per sensor`, detail: windowText(d.window_s) };
  }
  if (data.view === "incident") {
    const d = data as IncidentChartData;
    const name = d.series.find((s) => s.sensor === d.sensor)?.name ?? d.sensor;
    return {
      title: d.title,
      detail: `${d.t_text} on ${name}`,
      tone: d.severity === "critical" ? "bad" : d.severity === "warning" ? "warn" : undefined,
    };
  }
  if (data.view === "shift") return { title: `${data.name}: the whole shift`, detail: "" };
  if (data.view === "spectrum") {
    const d = data as SpectrumData;
    const when = d.event ? `vibration ${ago(d.event.t)}` : "live window";
    const peak = d.peak && d.peak.prominence >= 6
      ? `clear peak at ${d.peak.frequency_hz.toFixed(0)} Hz`
      : "no dominant frequency";
    return { title: `${d.name}: frequency spectrum`, detail: `${peak}, ${when}`, tone: d.peak && d.peak.prominence >= 6 ? "warn" : undefined };
  }
  if (data.view === "drift") {
    const d = data as DriftData;
    if (!d.fit || Math.abs(d.fit.rate_per_min) < 1) return { title: `${d.name}: drift and forecast`, detail: "No steady drift" };
    const minutes = d.forecast?.minutes_to_limit;
    const eta = minutes !== null && minutes !== undefined ? `, tolerance limit in about ${Math.round(minutes)} min` : "";
    return {
      title: `${d.name}: drift and forecast`,
      detail: `${signed(d.fit.rate_per_min, 1)} ${d.unit}/min${eta}`,
      tone: minutes !== null && minutes !== undefined && minutes < 10 ? "bad" : "warn",
    };
  }
  const h = data as HistoryData;
  if (h.view === "compare") {
    return { title: h.series.map((s) => s.name).join(" vs "), detail: `${windowText(h.window_s)}, same time axis` };
  }
  const s = h.series[0];
  const last = [...(s?.values ?? [])].reverse().find((v) => v !== null);
  return { title: `${s?.name ?? ""}: live signal`, detail: `${windowText(h.window_s)}, now ${signed(last ?? null, 2)} ${s?.unit ?? ""}` };
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
  const [shiftDetail, setShiftDetail] = useState<{ text: string; tone?: "warn" }>({ text: "loading…" });
  const mission = useStore((s) => s.mission);
  const isShift = panel.spec.view === "shift";
  const touring = Boolean(panel.tour);

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
    if (view === "shift") return undefined;
    // live views refresh; charts the copilot built are snapshots; tours hold the picture still
    const live = view === "timeseries" || view === "compare";
    const refreshMs = touring ? 0 : view === "drift" ? 4000 : live ? 1000 : 0;
    const load = async () => {
      try {
        const result = await loadView(panel.spec);
        if (!cancelled) {
          setData(result);
          setError(null);
          controller.rememberPanelData(panel.id, result);
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
  }, [panel.spec, depth, panel.leaving, touring]);

  useEffect(() => {
    const inst = instRef.current;
    if (!inst || !data) return;
    const steps = panel.tour ? panel.tour.steps.slice(0, panel.tour.shown) : [];
    const option = withAnnotations(buildOption(data), data, steps);
    if (firstDraw.current) {
      inst.setOption(option, { notMerge: true });
      firstDraw.current = false;
    } else {
      inst.setOption({ ...option, animation: false }, { notMerge: true });
    }
  }, [data, mission, panel.tour]);

  const info = isShift
    ? { title: `${controller.sensorName(panel.spec.sensors[0] ?? "s1")}: the whole shift`, detail: shiftDetail.text,
        tone: shiftDetail.tone }
    : describe(data, panel.spec.view);
  const tourText = panel.tour && panel.tour.shown > 0 ? `Step ${panel.tour.shown} of ${panel.tour.steps.length}` : null;

  return (
    <div ref={rootRef} className={`panel ${depth === 0 && !panel.leaving ? "panel-front" : ""}`}
         style={placement(depth, entered, panel.leaving)}>
      <span className="corner tl" />
      <span className="corner tr" />
      <span className="corner bl" />
      <span className="corner br" />
      <header className="panel-head">
        <div className="panel-title">{info.title}</div>
        <div className={`panel-detail ${info.tone ?? ""}`}>{error ? `error: ${error}` : tourText ?? info.detail}</div>
      </header>
      {isShift
        ? <ShiftChart panel={panel} front={depth === 0 && !panel.leaving}
                      onDetail={(text, tone) => setShiftDetail({ text, tone })} />
        : <div className="panel-chart" ref={chartRef} />}
    </div>
  );
}
