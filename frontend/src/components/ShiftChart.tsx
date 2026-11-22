// The shift timeline: scroll or pinch from eight hours down to single readings.
// The whole shift loads coarse; whatever is on screen is fetched again at the
// finest resolution the backend has (every reading, saved alert snapshots, or
// the min and max of each second). Thinned data is drawn as the average of each
// slice with a band from its lowest to its highest reading; zoomed in far enough,
// the band disappears into the line of single readings.
import { useEffect, useRef } from "react";
import { controller } from "../controller";
import { api } from "../lib/api";
import { echarts, shiftEventMarks, shiftOption, shiftSeriesData, type ShiftPoint } from "../lib/charts";
import { useStore, type StagePanel } from "../state/store";
import type { RangeData, ShiftData } from "../types";

type Point = ShiftPoint;

const AROUND: Record<string, [number, number]> = {
  vibration: [0.4, 1.6], drift: [600, 900], noise: [120, 600], invalid: [20, 45], out_of_range: [3, 9],
};

function toPoints(d: RangeData): Point[] {
  return d.t.map((t, i) => [t * 1000, d.v[i], d.lo?.[i] ?? d.v[i], d.hi?.[i] ?? d.v[i]]);
}

function spanText(seconds: number): string {
  if (seconds >= 5400) return `${(seconds / 3600).toFixed(1).replace(/\.0$/, "")} hours`;
  if (seconds >= 120) return `${Math.round(seconds / 60)} minutes`;
  if (seconds >= 10) return `${Math.round(seconds)} seconds`;
  return `${seconds.toFixed(seconds < 1 ? 2 : 1)} seconds`;
}

function resolutionText(d: RangeData): string {
  const every = (d.resolution_s * 1000).toFixed(0);
  const single = d.resolution_s < 0.01;
  if (d.source === "snapshot" && single) return `every reading, ${every} ms apart, saved around the alert`;
  if (d.source === "full" && single) return `every reading, ${every} ms apart`;
  const slice = d.resolution_s < 1 ? `${every} ms`
    : d.resolution_s >= 60 ? `${Math.round(d.resolution_s / 60)} min` : `${Math.round(d.resolution_s)} s`;
  return `line is the average of every ${slice}, band runs from lowest to highest`;
}

export function ShiftChart({ panel, front, onDetail }: {
  panel: StagePanel;
  front: boolean;
  onDetail: (text: string, tone?: "warn") => void;
}) {
  const hostRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.ECharts | null>(null);
  const base = useRef<Point[]>([]);
  const shownPoints = useRef<Point[]>([]);
  const meta = useRef<ShiftData | null>(null);
  const animating = useRef(false);
  const timer = useRef(0);
  const request = useRef(0);
  const sensor = panel.spec.sensors[0] ?? "s1";

  // visible window in seconds
  const visible = (): [number, number] | null => {
    const chart = chartRef.current;
    const pts = base.current;
    if (!chart || !pts.length) return null;
    const dz = (chart.getOption() as { dataZoom?: { start?: number; end?: number; startValue?: number; endValue?: number }[] })
      .dataZoom?.[0];
    const lo = pts[0][0];
    const hi = pts[pts.length - 1][0];
    const a = typeof dz?.startValue === "number" ? dz.startValue : lo + ((hi - lo) * (dz?.start ?? 0)) / 100;
    const b = typeof dz?.endValue === "number" ? dz.endValue : lo + ((hi - lo) * (dz?.end ?? 100)) / 100;
    return [a / 1000, b / 1000];
  };

  const loadVisible = async (window?: [number, number]) => {
    const w = window ?? visible();
    if (!w) return;
    const id = ++request.current;
    const pad = (w[1] - w[0]) * 0.15;
    try {
      const fine = await api.range(sensor, w[0] - pad, w[1] + pad, 1600);
      if (id !== request.current || !chartRef.current) return;
      const t0 = fine.t0 * 1000;
      const t1 = fine.t1 * 1000;
      const merged = [
        ...base.current.filter((p) => p[0] < t0),
        ...toPoints(fine),
        ...base.current.filter((p) => p[0] > t1),
      ];
      shownPoints.current = merged;
      const [line, low, spread] = shiftSeriesData(merged);
      chartRef.current.setOption({ series: [
        { data: line, markLine: { data: meta.current ? shiftEventMarks(meta.current, w[0], w[1]) : [] } },
        { data: low }, { data: spread },
      ] }, { lazyUpdate: true });
      onDetail(`Showing ${spanText(w[1] - w[0])}: ${resolutionText(fine)}`);
    } catch {
      /* keep the coarse view */
    }
  };

  const flyTo = (t0: number, t1: number) => {
    const chart = chartRef.current;
    const from = visible();
    if (!chart || !from) return;
    const [a0, b0] = from;
    const c0 = (a0 + b0) / 2;
    const c1 = (t0 + t1) / 2;
    const s0 = Math.log(Math.max(b0 - a0, 0.05));
    const s1 = Math.log(Math.max(t1 - t0, 0.05));
    // zoom out on the way when the jump is long, like a map
    const lift = Math.log(Math.max(Math.abs(c1 - c0) * 1.6, 1e-3));
    const peak = Math.max(s0, s1, lift);
    const started = performance.now();
    const duration = 1500;
    animating.current = true;
    // fetch the destination and some context around it while flying
    const reach = Math.max(t1 - t0, 0.05) * 2;
    void loadVisible([t0 - reach, t1 + reach]);
    const ease = (p: number) => (p < 0.5 ? 4 * p * p * p : 1 - (-2 * p + 2) ** 3 / 2);
    const frame = (now: number) => {
      const p = Math.min((now - started) / duration, 1);
      const e = ease(p);
      // span follows a curve through the "lift" height; the centre arrives a little early,
      // so the last part of the flight is a straight zoom into the destination
      const s = (1 - e) * (1 - e) * s0 + 2 * (1 - e) * e * peak + e * e * s1;
      const span = Math.exp(s);
      const c = c0 + (c1 - c0) * ease(Math.min(p / 0.78, 1));
      chart.dispatchAction({ type: "dataZoom", startValue: (c - span / 2) * 1000, endValue: (c + span / 2) * 1000 });
      if (p < 1) requestAnimationFrame(frame);
      else {
        animating.current = false;
        void loadVisible([t0, t1]);
      }
    };
    requestAnimationFrame(frame);
  };

  useEffect(() => {
    if (!hostRef.current) return;
    const chart = echarts.init(hostRef.current, undefined, { renderer: "canvas" });
    chartRef.current = chart;
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(hostRef.current);
    chart.on("datazoom", () => {
      if (animating.current) return;
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => void loadVisible(), 160);
    });
    chart.on("click", (raw) => {
      const params = raw as unknown as { componentType?: string; data?: { value?: string } };
      if (params.componentType !== "markLine") return;
      const event = meta.current?.events.find((e) => e.id === params.data?.value);
      if (!event) return;
      const [before, after] = AROUND[event.kind] ?? [30, 60];
      flyTo(event.t - before, event.t + after);
    });
    let cancelled = false;
    api.shift(sensor).then((data) => {
      if (cancelled) return;
      meta.current = data;
      base.current = toPoints(data);
      shownPoints.current = base.current;
      controller.rememberPanelData(panel.id, data);
      chart.setOption(shiftOption(data, base.current), { notMerge: true });
      onDetail(`Showing ${spanText(data.t1 - data.t0)}: ${resolutionText(data)}`);
      if (panel.spec.event_id) {
        const event = data.events.find((e) => e.id === panel.spec.event_id);
        if (event) {
          const [before, after] = AROUND[event.kind] ?? [30, 60];
          window.setTimeout(() => flyTo(event.t - before, event.t + after), 700);
        }
      }
    }).catch((err) => onDetail(`error: ${err instanceof Error ? err.message : String(err)}`, "warn"));
    return () => {
      cancelled = true;
      window.clearTimeout(timer.current);
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
    // the timeline is built once per panel
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // explicit requests to look somewhere (copilot tool, tour steps)
  const focusSeq = panel.focus?.seq;
  useEffect(() => {
    if (panel.focus && front) flyTo(panel.focus.t0, panel.focus.t1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusSeq]);

  const shown = panel.tour?.shown ?? 0;
  useEffect(() => {
    const step = panel.tour?.steps[shown - 1];
    if (step?.anchor?.type === "zoom" && step.anchor.t0 !== undefined && step.anchor.t1 !== undefined) {
      flyTo(step.anchor.t0, step.anchor.t1);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shown]);

  // the theme changed (mission mode): rebuild with the same data and zoom
  const mission = useStore((s) => s.mission);
  useEffect(() => {
    const chart = chartRef.current;
    const data = meta.current;
    if (!chart || !data) return;
    const w = visible();
    chart.setOption(shiftOption(data, shownPoints.current, w ?? undefined), { notMerge: true });
    if (w) chart.dispatchAction({ type: "dataZoom", startValue: w[0] * 1000, endValue: w[1] * 1000 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mission]);

  return <div className="panel-chart" ref={hostRef} />;
}
