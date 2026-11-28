import { useEffect, useRef, useState, type CSSProperties } from "react";
import { controller } from "../controller";
import { loadView } from "../lib/api";
import {
  boxplotOption, echarts, histogramOption, historyOption, partCountsOption, partsOption, withAnnotations, type ChartOption,
} from "../lib/charts";
import { posUnit, shownOf } from "../lib/units";
import { useStore, type StagePanel } from "../state/store";
import type { BoxplotData, HistogramData, HistoryData, PartCountsData, PartsData, ViewData } from "../types";

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
    case "histogram":
    case "part_heights": return histogramOption(data);
    case "boxplot": return boxplotOption(data);
    case "parts": return partsOption(data);
    case "part_counts": return partCountsOption(data);
    default: return historyOption(data as HistoryData);
  }
}

const GROUP_WORDS: Record<string, string> = { minute: "per minute", "10min": "per 10 minutes", hour: "per hour" };

function windowText(seconds: number): string {
  if (seconds >= 5400) return `Last ${Math.round(seconds / 3600)} hours`;
  return seconds >= 120 ? `Last ${Math.round(seconds / 60)} minutes` : `Last ${Math.round(seconds)} seconds`;
}

function runText(windowS: number | null | undefined): string {
  return windowS ? windowText(windowS) : "This run";
}

function describe(data: ViewData | null, fallbackTitle: string): { title: string; detail: string; tone?: "warn" | "bad" } {
  if (!data) return { title: fallbackTitle, detail: "loading…" };
  if (data.view === "parts") {
    const d = data as PartsData;
    const c = d.counts;
    if (!c.parts) return { title: `${d.name}: height of every part`, detail: "no parts yet" };
    const share = Math.round((100 * c.good) / c.parts);
    return { title: `${d.name}: height of every part`,
      detail: `${runText(d.window_s)}, ${c.parts} parts, ${share}% the taught part`, tone: c.different ? "warn" : undefined };
  }
  if (data.view === "part_counts") {
    const d = data as PartCountsData;
    const good = d.good.reduce((a, b) => a + b, 0);
    const different = d.different.reduce((a, b) => a + b, 0);
    return { title: `${d.name}: parts ${GROUP_WORDS[d.group_by] ?? ""}`,
      detail: `${runText(d.window_s)}, ${good} good, ${different} different`, tone: different ? "warn" : undefined };
  }
  if (data.view === "part_heights") {
    const d = data as HistogramData;
    const s = d.series[0];
    if (!s) return { title: "How the part heights are spread", detail: "not enough parts" };
    return { title: `${s.name}: how the part heights are spread`,
      detail: `${runText(d.window_s)}, ${s.n} parts${s.capability ? `, Cpk ${s.cpk.toFixed(1)}` : ""}`,
      tone: s.outside > 0 ? "warn" : undefined };
  }
  if (data.view === "histogram") {
    const d = data as HistogramData;
    const s = d.series[0];
    const names = d.series.map((x) => x.name).join(" and ");
    if (!s) return { title: "How the readings are spread", detail: "not enough data" };
    // Cpk judges a part's tolerance; a sensor's measuring range gets the number of readings instead
    if (s.capability === false) {
      return { title: `${names}: how the readings are spread`,
        detail: `${windowText(d.window_s)}, ${s.n.toLocaleString()} readings`, tone: s.outside > 0 ? "warn" : undefined };
    }
    return { title: `${names}: how the readings are spread`,
      detail: `${windowText(d.window_s)}, Cpk ${s.cpk.toFixed(1)}`, tone: s.cpk < 1.33 ? "warn" : undefined };
  }
  if (data.view === "boxplot") {
    const d = data as BoxplotData;
    return { title: `Spread ${GROUP_WORDS[d.group_by] ?? ""}`,
      detail: `${windowText(d.window_s)}, from ${d.per}` };
  }
  const h = data as HistoryData;
  const s = h.series[0];
  const last = [...(s?.values ?? [])].reverse().find((v) => v !== null);
  const shown = shownOf(s?.unit ?? "", s?.display, s?.limit_word);
  return { title: `${s?.name ?? ""}: live signal`, detail: `${windowText(h.window_s)}, now ${posUnit(last ?? null, shown, 2)}` };
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
  const mission = useStore((s) => s.mission);
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
    // the live signal refreshes; charts the copilot built are snapshots; tours hold the picture still
    const refreshMs = touring ? 0 : view === "timeseries" ? 1000 : 0;
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

  const info = describe(data, panel.spec.view);
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
      <div className="panel-chart" ref={chartRef} />
    </div>
  );
}
