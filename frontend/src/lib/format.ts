import type { EventKind, Severity } from "../types";

export function signed(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const fixed = value.toFixed(digits);
  if (Number(fixed) === 0) return (0).toFixed(digits);
  return value > 0 ? `+${fixed}` : fixed.replace("-", "−");
}

export function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(digits).replace("-", "−");
}

export function relSeconds(value: number): string {
  if (Math.abs(value) < 0.5) return "now";
  const sign = value < 0 ? "−" : "+";
  const abs = Math.abs(value);
  if (abs >= 3600 && abs % 3600 === 0) return `${sign}${abs / 3600} h`;
  if (abs >= 60 && abs % 60 === 0) return `${sign}${abs / 60} min`;
  if (abs >= 120) return `${sign}${(abs / 60).toFixed(1)} min`;
  return `${sign}${Math.round(abs)} s`;
}

export function clock(t: number): string {
  return new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function ago(t: number, now = Date.now() / 1000): string {
  const s = Math.max(0, Math.round(now - t));
  if (s < 5) return "just now";
  if (s < 60) return `${s} s ago`;
  const m = Math.round(s / 60);
  return m < 60 ? `${m} min ago` : `${Math.round(m / 60)} h ago`;
}

export const KIND_LABEL: Record<EventKind, string> = {
  vibration: "Vibration",
  noise: "Noise",
  drift: "Drift",
  out_of_range: "Out of tolerance",
  invalid: "Invalid readings",
  wrong_part: "Different part",
};

/** Short names for the alert lines on the timelines. */
export const KIND_SHORT: Record<string, string> = {
  vibration: "knock", noise: "noise", drift: "drift", out_of_range: "out of tol.", invalid: "no signal", wrong_part: "other part",
};

/**
 * What an alert for a target that left its limits is called: a sensor with a data sheet has a measuring range,
 * a part has a tolerance. Set once when the configuration arrives.
 */
export function setRangeWording(measuring: boolean): void {
  KIND_LABEL.out_of_range = measuring ? "Out of measuring range" : "Out of tolerance";
  KIND_SHORT.out_of_range = measuring ? "out of range" : "out of tol.";
}

export const SEVERITY_LABEL: Record<Severity, string> = {
  info: "Info",
  warning: "Warning",
  critical: "Critical",
};

/** Seconds from the start of a recording as m:ss.s, or h:mm:ss.s past an hour. */
export function recordingClock(seconds: number): string {
  const tenths = Math.max(0, Math.round(seconds * 10));
  const h = Math.floor(tenths / 36000);
  const m = Math.floor((tenths % 36000) / 600);
  const s = (tenths % 600) / 10;
  const head = h ? `${h}:${String(m).padStart(2, "0")}` : String(m).padStart(2, "0");
  return `${head}:${s.toFixed(1).padStart(4, "0")}`;
}
