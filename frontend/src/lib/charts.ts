// ECharts setup and the option builders for the four chart views.
import { LineChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import type { CockpitEvent, DriftData, HistoryData, Series, Severity, SpectrumData, Tolerance } from "../types";
import { KIND_LABEL, relSeconds } from "./format";

echarts.use([
  LineChart,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  MarkPointComponent,
  TooltipComponent,
  CanvasRenderer,
]);

export { echarts };
export type ChartOption = echarts.EChartsCoreOption;

export const COLORS = {
  text: "#9fb3c8",
  dim: "#5b7083",
  grid: "rgba(120, 180, 220, 0.09)",
  axis: "rgba(120, 180, 220, 0.25)",
  cyan: "#22d3ee",
  violet: "#a78bfa",
  amber: "#fbbf24",
  red: "#fb4f6b",
  green: "#34d399",
  series: ["#22d3ee", "#a78bfa", "#34d399", "#fbbf24"],
};

const SEVERITY_FILL: Record<Severity, string> = {
  info: "rgba(34, 211, 238, 0.14)",
  warning: "rgba(251, 191, 36, 0.16)",
  critical: "rgba(251, 79, 107, 0.2)",
};

const FONT = "'JetBrains Mono', 'Cascadia Mono', 'SFMono-Regular', Consolas, monospace";
const HIDDEN = "_"; // series whose name starts with this stay out of tooltips and legends

function hexToRgba(hex: string, alpha: number): string {
  const n = Number.parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

function tooltip(unit: string): Record<string, unknown> {
  return {
    trigger: "axis",
    backgroundColor: "rgba(6, 14, 24, 0.94)",
    borderColor: "rgba(34, 211, 238, 0.35)",
    textStyle: { color: "#e6f6ff", fontFamily: FONT, fontSize: 12 },
    axisPointer: { lineStyle: { color: "rgba(34, 211, 238, 0.5)" } },
    formatter: (params: unknown) => {
      const list = (Array.isArray(params) ? params : [params]) as {
        seriesName: string;
        value: [number, number | null];
        marker: string;
      }[];
      const visible = list.filter((p) => !p.seriesName.startsWith(HIDDEN) && p.value?.[1] !== null);
      if (!visible.length) return "";
      const rows = visible.map((p) => `${p.marker}${p.seriesName}: <b>${Number(p.value[1]).toFixed(2)}</b> ${unit}`);
      return rows.join("<br/>");
    },
  };
}

function base(unit: string): ChartOption {
  return {
    backgroundColor: "transparent",
    textStyle: { color: COLORS.text, fontFamily: FONT },
    animation: true,
    animationDuration: 900,
    animationEasing: "cubicOut",
    animationDurationUpdate: 300,
    tooltip: tooltip(unit),
  };
}

function niceRange(lo: number, hi: number): [number, number] {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [-1, 1];
  if (hi - lo < 1e-6) {
    lo -= 1;
    hi += 1;
  }
  const pad = (hi - lo) * 0.12;
  const raw = (hi - lo + 2 * pad) / 5;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= raw) ?? raw;
  return [Math.floor((lo - pad) / step) * step, Math.ceil((hi + pad) / step) * step];
}

function extent(values: (number | null)[]): [number, number] {
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of values) {
    if (v === null || Number.isNaN(v)) continue;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  }
  return [lo, hi];
}

function valueAxis(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    type: "value",
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: COLORS.dim, fontSize: 11 },
    splitLine: { lineStyle: { color: COLORS.grid } },
    ...extra,
  };
}

function glow(color: string, width = 1.6): Record<string, unknown> {
  return { color, width, shadowBlur: 10, shadowColor: color };
}

function toleranceMarks(tolerance: Tolerance, unit: string): Record<string, unknown>[] {
  const label = (v: number) => `tol ${v > 0 ? "+" : ""}${v} ${unit}`;
  return [
    { yAxis: tolerance.high, label: { formatter: label(tolerance.high) } },
    { yAxis: tolerance.low, label: { formatter: label(tolerance.low), position: "insideEndBottom" } },
  ];
}

function eventAreas(events: CockpitEvent[], tNow: number, withLabel: boolean): Record<string, unknown> {
  return {
    silent: true,
    data: events.map((e) => [
      {
        xAxis: e.t - tNow - 1.2,
        itemStyle: { color: SEVERITY_FILL[e.severity] },
        label: withLabel
          ? { show: true, position: "insideTop", color: COLORS.amber, fontSize: 10, formatter: KIND_LABEL[e.kind] }
          : { show: false },
      },
      { xAxis: e.t - tNow + 1.2 },
    ]),
  };
}

/**
 * One sensor's trace. Thinned data arrives as min/max pairs; drawn as a faint
 * envelope (so short spikes stay visible) plus a bright centre line (so long
 * windows stay readable instead of turning into a solid wall).
 */
function traceSeries(s: Series, tNow: number, color: string, axis: number, extras: Record<string, unknown>) {
  const thinned = s.values.length > 400 && s.dt > 0.004;
  const indexes = { xAxisIndex: axis, yAxisIndex: axis };
  const raw = s.values.map((v, i) => [s.t0 + i * s.dt - tNow, v]);
  if (!thinned) {
    return [{
      type: "line", name: s.name, ...indexes, showSymbol: false, connectNulls: false, data: raw,
      lineStyle: glow(color), itemStyle: { color }, emphasis: { disabled: true }, ...extras,
    }];
  }
  const middle: [number, number | null][] = [];
  for (let i = 0; i + 1 < s.values.length; i += 2) {
    const a = s.values[i];
    const b = s.values[i + 1];
    middle.push([s.t0 + (i + 0.5) * s.dt - tNow, a === null || b === null ? null : (a + b) / 2]);
  }
  return [
    {
      type: "line", name: `${HIDDEN}envelope`, ...indexes, data: raw, showSymbol: false, silent: true,
      connectNulls: false, emphasis: { disabled: true }, lineStyle: { color: hexToRgba(color, 0.3), width: 1 },
    },
    {
      type: "line", name: s.name, ...indexes, showSymbol: false, connectNulls: false, data: middle,
      lineStyle: glow(color, 1.6), itemStyle: { color }, emphasis: { disabled: true }, ...extras,
    },
  ];
}

/** Timeseries (one sensor) and compare (sensors stacked on one time axis). */
export function historyOption(data: HistoryData): ChartOption {
  const unit = data.series[0]?.unit ?? "";
  const n = Math.max(1, data.series.length);
  // Stacked grids for compare: percentages, since ECharts has no calc().
  const topPct = 5;
  const bottomPct = 10;
  const gapPct = 7;
  const heightPct = (100 - topPct - bottomPct - gapPct * (n - 1)) / n;
  const grids: Record<string, unknown>[] = [];
  const xAxes: Record<string, unknown>[] = [];
  const yAxes: Record<string, unknown>[] = [];
  const series: Record<string, unknown>[] = [];

  data.series.forEach((s, index) => {
    const color = COLORS.series[index % COLORS.series.length];
    grids.push(
      n === 1
        ? { left: 70, right: 30, top: 22, bottom: 38 }
        : { left: 70, right: 30, top: `${topPct + index * (heightPct + gapPct)}%`, height: `${heightPct}%` },
    );
    const last = index === n - 1;
    xAxes.push({
      type: "value",
      gridIndex: index,
      min: -data.window_s,
      max: 0,
      axisLine: { lineStyle: { color: COLORS.axis } },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { show: last, color: COLORS.dim, fontSize: 11, formatter: (v: number) => relSeconds(v) },
    });

    const [lo, hi] = extent(s.values);
    // Show the tolerance band only when the signal gets at least halfway there;
    // otherwise it would squash the trace into a flat line.
    const mid = (s.tolerance.high + s.tolerance.low) / 2;
    const nearLimit = hi >= mid + (s.tolerance.high - mid) / 2 || lo <= mid - (mid - s.tolerance.low) / 2;
    const [yMin, yMax] = nearLimit
      ? niceRange(Math.min(lo, s.tolerance.low), Math.max(hi, s.tolerance.high))
      : niceRange(lo, hi);
    yAxes.push(valueAxis({
      gridIndex: index,
      min: yMin,
      max: yMax,
      splitNumber: n > 1 ? 3 : 5,
      name: n > 1 ? s.name : "",
      nameLocation: "middle",
      nameGap: 48,
      nameTextStyle: { color, fontSize: 12, fontWeight: "bold" },
    }));

    // Moments only (a drift is not a moment), and only this sensor's events.
    const mine = data.events.filter(
      (e) => e.kind !== "drift" && (e.sensor === s.sensor || e.related_sensors.includes(s.sensor)),
    );
    const extras: Record<string, unknown> = { markArea: eventAreas(mine, data.t_now, true) };
    if (nearLimit) {
      extras.markLine = {
        silent: true,
        symbol: "none",
        lineStyle: { color: COLORS.red, type: "dashed", width: 1, opacity: 0.8 },
        label: { color: COLORS.red, fontSize: 10, position: "insideEndTop" },
        data: toleranceMarks(s.tolerance, unit),
      };
    }
    series.push(...traceSeries(s, data.t_now, color, index, extras));
  });

  return {
    ...base(unit),
    grid: grids,
    xAxis: xAxes,
    yAxis: yAxes,
    series,
    axisPointer: n > 1 ? { link: [{ xAxisIndex: "all" }] } : undefined,
  };
}

export function spectrumOption(data: SpectrumData): ChartOption {
  const points = data.freqs.map((f, i) => [f, data.amps[i]]);
  const peak = data.peak && data.peak.prominence >= 6 ? data.peak : null;
  const top = Math.max(...(data.amps.filter((a) => a !== null) as number[]), 0.1);
  return {
    ...base(data.unit),
    grid: { left: 70, right: 36, top: 34, bottom: 40 },
    xAxis: {
      type: "value",
      min: 0,
      max: Math.round((data.freqs[data.freqs.length - 1] as number) ?? 100),
      name: "Hz",
      nameTextStyle: { color: COLORS.dim, fontSize: 11 },
      axisLine: { lineStyle: { color: COLORS.axis } },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: { color: COLORS.dim, fontSize: 11 },
    },
    yAxis: valueAxis({
      min: 0,
      max: niceRange(0, top * 1.12)[1],
      name: `amplitude (${data.unit})`,
      nameTextStyle: { color: COLORS.dim, fontSize: 11, align: "left" },
    }),
    series: [
      {
        type: "line",
        name: "Amplitude",
        data: points,
        showSymbol: false,
        smooth: 0.2,
        lineStyle: glow(peak ? COLORS.cyan : COLORS.violet),
        itemStyle: { color: COLORS.cyan },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: "rgba(34, 211, 238, 0.35)" },
            { offset: 1, color: "rgba(34, 211, 238, 0.02)" },
          ]),
        },
        markPoint: peak
          ? {
              symbol: "pin",
              symbolSize: 56,
              itemStyle: { color: COLORS.red, shadowBlur: 16, shadowColor: COLORS.red },
              label: { color: "#fff", fontSize: 11, fontWeight: "bold", formatter: `${Math.round(peak.frequency_hz)} Hz` },
              data: [{ coord: [peak.frequency_hz, peak.amplitude] }],
            }
          : undefined,
        markLine: peak
          ? {
              silent: true,
              symbol: "none",
              lineStyle: { color: COLORS.red, type: "dashed", width: 1 },
              label: { show: false },
              data: [{ xAxis: peak.frequency_hz }],
            }
          : undefined,
      },
    ],
  };
}

export function driftOption(data: DriftData): ChartOption {
  const history = data.t_rel.map((t, i) => [t === null ? null : t / 60, data.values[i]]);
  const forecast = data.forecast?.points.map(([t, v]) => [t / 60, v]) ?? [];
  const [lo, hi] = extent([...data.values, ...forecast.map((p) => p[1] as number)]);
  const [yMin, yMax] = niceRange(Math.min(lo, data.tolerance.low), Math.max(hi, data.tolerance.high));
  const minX = Math.min(-1, ...history.map((p) => (p[0] as number) ?? 0));
  const maxX = forecast.length ? Math.max(1, forecast[forecast.length - 1][0] as number) : 1;
  const crossing = data.forecast?.crossing;
  const minutes = data.forecast?.minutes_to_limit;
  const tolLabel = (v: number) => `tol ${v > 0 ? "+" : ""}${v} ${data.unit}`;

  return {
    ...base(data.unit),
    grid: { left: 70, right: 36, top: 26, bottom: 40 },
    xAxis: {
      type: "value",
      min: Math.floor(minX),
      max: Math.ceil(maxX),
      axisLine: { lineStyle: { color: COLORS.axis } },
      axisTick: { show: false },
      splitLine: { show: false },
      axisLabel: {
        color: COLORS.dim,
        fontSize: 11,
        formatter: (v: number) => (v === 0 ? "now" : `${v > 0 ? "+" : "−"}${Math.abs(v)} min`),
      },
    },
    yAxis: valueAxis({ min: yMin, max: yMax }),
    series: [
      {
        type: "line",
        name: "Measured",
        data: history,
        showSymbol: false,
        lineStyle: glow(COLORS.cyan),
        itemStyle: { color: COLORS.cyan },
        markLine: {
          silent: true,
          symbol: "none",
          data: [
            { xAxis: 0, lineStyle: { color: "rgba(230, 246, 255, 0.35)", type: "solid", width: 1 }, label: { show: false } },
            { yAxis: data.tolerance.high, lineStyle: { color: COLORS.red, type: "dashed", width: 1 },
              label: { color: COLORS.red, fontSize: 10, position: "insideStartTop", formatter: tolLabel(data.tolerance.high) } },
            { yAxis: data.tolerance.low, lineStyle: { color: COLORS.red, type: "dashed", width: 1 },
              label: { color: COLORS.red, fontSize: 10, position: "insideStartBottom", formatter: tolLabel(data.tolerance.low) } },
          ],
        },
      },
      {
        type: "line",
        name: "Forecast",
        data: forecast,
        showSymbol: false,
        lineStyle: { color: COLORS.amber, width: 2, type: [6, 6], shadowBlur: 8, shadowColor: COLORS.amber },
        itemStyle: { color: COLORS.amber },
        markPoint: crossing
          ? {
              symbol: "circle",
              symbolSize: 12,
              itemStyle: { color: COLORS.red, shadowBlur: 18, shadowColor: COLORS.red },
              label: {
                show: true,
                position: "left",
                distance: 12,
                color: COLORS.red,
                fontSize: 12,
                fontWeight: "bold",
                backgroundColor: "rgba(5, 12, 21, 0.9)",
                padding: [3, 7],
                borderRadius: 4,
                formatter: `limit in ~${Math.round(minutes ?? crossing.t_rel / 60)} min`,
              },
              data: [{ coord: [crossing.t_rel / 60, crossing.value] }],
            }
          : undefined,
      },
    ],
  };
}
