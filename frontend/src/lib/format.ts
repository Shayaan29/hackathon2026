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
  if (abs >= 120) return `${sign}${Math.round(abs / 60)} min`;
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
};

export const SEVERITY_LABEL: Record<Severity, string> = {
  info: "Info",
  warning: "Warning",
  critical: "Critical",
};
