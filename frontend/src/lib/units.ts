// How a position is written. A sensor with a display unit (the optoNCDT shows millimetres from the start of its
// measuring range) is read in that unit; everything the cockpit computes stays in the signal unit (µm), because
// noise and vibration of a few µm would vanish in millimetre numbers.
import type { Display, RangeZone, SensorInfo, Tolerance } from "../types";
import { num, signed } from "./format";

export interface Shown {
  /** what to write after a position: "mm", or the signal unit */
  unit: string;
  display: Display | null;
  /** true when the limits are the measuring range of a known sensor, not the tolerance of a part */
  measuring: boolean;
}

export function shownOf(unit: string, display?: Display | null, limitWord?: string): Shown {
  return { unit: display ? display.unit : unit, display: display ?? null, measuring: limitWord === "measuring range" };
}

export function shownFor(sensor: Pick<SensorInfo, "unit" | "display" | "limit_word"> | undefined): Shown {
  return shownOf(sensor?.unit ?? "", sensor?.display, sensor?.limit_word);
}

/** A position without its unit: 5.0012 for a display sensor, +6.00 for a sensor shown in the signal unit. */
export function pos(v: number | null | undefined, s: Shown, digits = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "–";
  if (!s.display) return signed(v, digits);
  return num(v * s.display.scale, s.display.decimals);
}

export function posUnit(v: number | null | undefined, s: Shown, digits = 2): string {
  return `${pos(v, s, digits)} ${s.unit}`;
}

/** A limit without trailing zeros: 10 mm, -2.5 mm. */
export function plain(v: number, s: Shown): string {
  const value = s.display ? v * s.display.scale : v;
  return `${Number(value.toFixed(s.display ? s.display.decimals : 2))} ${s.unit}`.replace("-", "−");
}

/** Tick labels for a value axis whose ticks are about `step` apart (in signal units). */
export function axisFormatter(s: Shown, step?: number): ((v: number) => string) | undefined {
  const display = s.display;
  if (!display) return undefined;
  let digits = display.decimals;
  if (step && step > 0) digits = Math.min(display.decimals, Math.max(0, Math.ceil(-Math.log10(step * display.scale) - 1e-9)));
  return (v: number) => num(v * display.scale, digits);
}

/** The label on a limit line of a chart: "tol +50 µm" for a part, "range end 10 mm" for a measuring range. */
export function limitLabel(v: number, s: Shown, edge: "low" | "high"): string {
  if (s.measuring) return `${edge === "low" ? "range start" : "range end"} ${plain(v, s)}`;
  return s.display ? `tol ${plain(v, s)}` : `tol ${v > 0 ? "+" : ""}${v} ${s.unit}`;
}

/** The chart shows the limits only when the signal comes near them, otherwise they would flatten the trace. */
export function nearLimit(tolerance: Tolerance, lo: number, hi: number, s: Shown): boolean {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return false;
  if (s.measuring) {
    const margin = (tolerance.high - tolerance.low) * 0.1;
    return hi >= tolerance.high - margin || lo <= tolerance.low + margin;
  }
  const mid = (tolerance.high + tolerance.low) / 2;
  return hi >= mid + (tolerance.high - mid) / 2 || lo <= mid - (mid - tolerance.low) / 2;
}

/** Where a reading lies in the measuring range, like the state LED of the sensor. */
export function zoneOf(v: number | null, sensor: Pick<SensorInfo, "spec">): RangeZone | null {
  const z = sensor.spec?.zones_um;
  if (!z || v === null || !Number.isFinite(v)) return null;
  if (v >= z.low && v <= z.high) return "standard";
  if (v >= z.ext_low && v < z.low) return "before";
  if (v > z.high && v <= z.ext_high) return "after";
  return "outside";
}

export const ZONE_WORDS: Record<RangeZone, string> = {
  standard: "in the measuring range",
  before: "closer than the range (extended)",
  after: "beyond the range (extended)",
  outside: "outside the extended range",
};
