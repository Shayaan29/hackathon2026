// Message and data shapes shared with the backend (see docs/CONTRACT.md).

export type Severity = "info" | "warning" | "critical";
export type EventKind = "vibration" | "noise" | "drift" | "out_of_range" | "invalid";
export type SensorState = "ok" | "warning" | "critical";
export type ChartView = "timeseries" | "compare" | "spectrum" | "drift";

export interface Tolerance {
  low: number;
  high: number;
}

export interface SettingSpec {
  default: string | number;
  description: string;
  options?: (string | number)[];
  min?: number;
  max?: number;
}

export interface SensorInfo {
  id: string;
  name: string;
  model: string;
  unit: string;
  nominal: number;
  tolerance: Tolerance;
  fixture: string;
  source: string;
  settings: Record<string, SettingSpec>;
}

export interface CockpitConfig {
  title: string;
  team: string;
  assistant_name: string;
  language: string;
  sensors: SensorInfo[];
  voice: { enabled: boolean; model: string };
  token_required: boolean;
  alert_prefix: string;
  demo_kinds: string[];
  version: string;
}

export interface CockpitEvent {
  type: "event";
  id: string;
  sensor: string;
  kind: EventKind;
  severity: Severity;
  t: number;
  numbers: Record<string, number | string>;
  summary: string;
  forecast_min: number | null;
  related_sensors: string[];
}

export interface SensorStatus {
  latest: number | null;
  mean_1s: number | null;
  noise: number | null;
  noise_ratio: number | null;
  state: SensorState;
  active: EventKind[];
  source: { type: string; fallback: boolean; error: string | null };
  read_error: string | null;
  settings: Record<string, string | number>;
}

export interface StatusMessage {
  type: "status";
  t: number;
  uptime_s: number;
  clients: { cockpit: number; phone: number };
  sensors: Record<string, SensorStatus>;
}

export interface SamplesMessage {
  type: "samples";
  sensor: string;
  t0: number;
  dt: number;
  unit: string;
  values: (number | null)[];
}

export interface SettingChangedMessage {
  type: "setting_changed";
  sensor: string;
  name: string;
  old: string | number;
  new: string | number;
  t: number;
}

export interface HelloMessage {
  type: "hello";
  config: CockpitConfig;
  events: CockpitEvent[];
  status: StatusMessage;
}

export type ServerMessage =
  | HelloMessage
  | StatusMessage
  | SamplesMessage
  | CockpitEvent
  | SettingChangedMessage
  | ShakeMessage
  | { type: "report"; id: string; title: string }
  | { type: "pong"; t: number };

export interface ShakeMessage {
  type: "shake";
  name: string;
  sensor: string;
  intensity: number;
  t: number;
}

export interface SettingChange {
  name: string;
  from: string | number | null;
  to: string | number;
}

export interface AssistantPlan {
  sensor: string;
  name: string;
  unit: string;
  status: "ok" | "improve";
  summary: string;
  current: Record<string, string | number>;
  measured: {
    noise: number | null;
    normal_noise: number | null;
    noise_limit: number;
    noise_target: number;
    invalid_share: number;
    watch_frequency_hz: number;
    bandwidth_hz: number | null;
  };
  proposal: SettingChange[];
  reasons: string[];
  expected: { noise: number | null; bandwidth_hz: number | null };
  preview: { window_s: number; before: (number | null)[]; after: (number | null)[] };
  t: number;
}

export interface ChartSpec {
  view: ChartView;
  sensors: string[];
  window_s?: number;
  event_id?: string;
}

export interface Series {
  sensor: string;
  name: string;
  unit: string;
  t0: number;
  dt: number;
  values: (number | null)[];
  tolerance: Tolerance;
}

export interface HistoryData {
  view: "timeseries" | "compare";
  window_s: number;
  t_now: number;
  series: Series[];
  events: CockpitEvent[];
}

export interface SpectrumData {
  view: "spectrum";
  sensor: string;
  name: string;
  unit: string;
  window_s: number;
  t_start: number;
  event: CockpitEvent | null;
  freqs: (number | null)[];
  amps: (number | null)[];
  peak: { frequency_hz: number; amplitude: number; prominence: number } | null;
}

export interface DriftData {
  view: "drift";
  sensor: string;
  name: string;
  unit: string;
  window_s: number;
  tolerance: Tolerance;
  t_rel: (number | null)[];
  values: (number | null)[];
  fit: { rate_per_min: number; r2: number; from_rel: number } | null;
  forecast: {
    points: [number, number][];
    minutes_to_limit: number | null;
    crossing: { t_rel: number; value: number } | null;
    horizon_s: number;
  } | null;
}

export type ViewData = HistoryData | SpectrumData | DriftData;

export interface RealtimeSession {
  client_secret: string;
  expires_at: number | null;
  model: string;
  calls_url: string;
  session_updates: Record<string, unknown>[];
  alert_prefix: string;
}

export type VoiceMode = "off" | "connecting" | "ready" | "error";
export type VoiceActivity = "idle" | "listening" | "thinking" | "speaking";
