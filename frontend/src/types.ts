// Message and data shapes shared with the backend (see docs/CONTRACT.md).

export type Severity = "info" | "warning" | "critical";
export type EventKind = "vibration" | "noise" | "drift" | "out_of_range" | "invalid" | "wrong_part";
export type SensorState = "ok" | "warning" | "critical";
export type ChartView = "timeseries" | "compare" | "spectrum" | "drift" | ExploreChart | "shift" | "incident";
export type ExploreChart = "histogram" | "boxplot" | "scatter" | "bar";

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

/** Positions are shown in the sensor's own unit (mm); the signal itself stays in `unit` (µm). */
export interface Display {
  unit: string;
  /** signal unit times scale is the shown unit */
  scale: number;
  decimals: number;
}

/** The data sheet of a known sensor model (see backend/app/sensor_specs.py). Distances are from the start of the range. */
export interface SensorSpecInfo {
  family: string;
  model: string;
  title: string;
  measuring_range_mm: number;
  start_mm: number;
  mid_mm: number;
  end_mm: number;
  /** extended range, measuring range and middle in signal units (µm) from the start of the measuring range */
  zones_um: { ext_low: number; low: number; mid: number; high: number; ext_high: number };
  linearity_um: number;
  repeatability_um: number;
  temperature_um_per_k: number;
  manual: string;
}

/** Where a reading lies, in the words of the sensor's state LED. */
export type RangeZone = "standard" | "before" | "after" | "outside";

export interface SensorInfo {
  id: string;
  name: string;
  model: string;
  unit: string;
  nominal: number;
  tolerance: Tolerance;
  /** part: the tolerance of the workpiece. measuring_range: the measuring range of the sensor */
  tolerance_kind: "part" | "measuring_range";
  /** what the limits are called in texts: "tolerance" or "measuring range" */
  limit_word: string;
  display: Display | null;
  spec: SensorSpecInfo | null;
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
  part_rpm?: number;
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

/** Connection of a real sensor (the optoNCDT 5500 over Ethernet). Absent for dummy data. */
export interface LiveStatus {
  kind: string;
  /** offline: cannot connect. no_data: connected, nothing arrives. invalid: readings arrive but are mostly invalid. */
  state: "offline" | "no_data" | "streaming" | "invalid";
  message: string;
  host: string;
  port: number;
  rate_hz: number | null;
  article: number | null;
  serial: number | null;
  samples: number;
  invalid_percent: number | null;
  last_status: string | null;
  reference_mm: number | null;
  /** how the readings were counted: range_start (as the sensor reports), auto, mid or a number */
  reference?: string;
  /** why readings were invalid in the last seconds: status code name to share (0 to 1) */
  invalid_reasons?: Record<string, number>;
  intensity_percent: number | null;
  temperature_c: number | null;
}

/** The part check: one taught part, everything else is flagged. Positions are in the signal unit (µm). */
export interface PartState {
  supported: boolean;
  /** no_reference: nothing taught. match: the taught part. different: another part. empty: the taught empty plate. */
  state: "no_reference" | "waiting" | "match" | "different" | "empty";
  /** the last 0.4 s were steady enough to tell what lies under the sensor */
  steady: boolean;
  position_um: number | null;
  message: string;
  reference: {
    center_um: number; half_width_um: number; margin_um: number; parts: number;
    low_um: number; high_um: number; taught_at: number;
  } | null;
  empty_um: number | null;
  /** height of the taught part above the taught empty plate */
  height_um: number | null;
  alerts: number;
}

export interface SensorStatus {
  latest: number | null;
  mean_1s: number | null;
  noise: number | null;
  noise_ratio: number | null;
  state: SensorState;
  active: EventKind[];
  source: { type: string; fallback: boolean; error: string | null };
  live?: LiveStatus | null;
  part?: PartState;
  read_error: string | null;
  settings: Record<string, string | number>;
}

export interface StatusMessage {
  type: "status";
  t: number;
  uptime_s: number;
  clients: { cockpit: number; phone: number };
  sensors: Record<string, SensorStatus>;
  recording?: RecordingPublic;
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
  | RecordingMessage
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
  /** advice: the sensor's own settings cannot be changed from the cockpit, so only what to check */
  status: "ok" | "improve" | "advice";
  summary: string;
  /** what the comparison is based on, for example the data sheet linearity */
  basis?: string;
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
  group_by?: "minute" | "10min" | "hour" | "sensor";
  metric?: "noise" | "mean" | "range" | "events";
}

/** What to point at while one explanation step is spoken (see backend insights.py). */
export interface Anchor {
  type: "x" | "point" | "xspan" | "yspan" | "grid" | "category" | "line" | "zoom";
  t?: number;
  x?: number;
  y?: number;
  t0?: number;
  t1?: number;
  x0?: number;
  x1?: number;
  y0?: number;
  y1?: number;
  index?: number;
  sensor?: string;
  label?: string;
}

export interface InsightStep {
  text: string;
  anchor: Anchor | null;
}

export interface HistogramData {
  view: "histogram";
  window_s: number;
  per: string;
  series: {
    sensor: string; name: string; unit: string; nominal: number; edges: number[]; counts: number[];
    n: number; mean: number; std: number; p05: number; p95: number; tolerance: Tolerance; cp: number; cpk: number;
    /** readings outside the tolerance (or measuring range), and the one furthest from nominal */
    outside: number; worst: number | null;
    display: Display | null; limit_word: string;
    /** false for a sensor's measuring range: Cp and Cpk judge a part's tolerance only */
    capability: boolean;
  }[];
  insights: InsightStep[];
}

export interface BoxplotData {
  view: "boxplot";
  window_s: number;
  group_by: string;
  per: string;
  unit: string;
  display: Display | null;
  sensors: string[];
  categories: string[];
  boxes: number[][];
  counts: number[];
  insights: InsightStep[];
}

export interface ScatterData {
  view: "scatter";
  window_s: number;
  x: { sensor: string; name: string; unit: string; display: Display | null };
  y: { sensor: string; name: string; unit: string; display: Display | null };
  points: [number, number][];
  r: number;
  slope: number;
  highlight: { t: number; r: number; lag_ms: number; points: [number, number][] } | null;
  insights: InsightStep[];
}

export interface BarData {
  view: "bar";
  metric: string;
  window_s: number;
  unit: string;
  display: Display | null;
  categories: string[];
  values: number[];
  label: string;
  insights: InsightStep[];
}

export interface RangeData {
  view: "shift";
  sensor: string;
  name: string;
  unit: string;
  display: Display | null;
  limit_word: string;
  tolerance: Tolerance;
  nominal: number;
  t0: number;
  t1: number;
  t_now: number;
  oldest: number;
  fast_start: number;
  t: number[];
  /** average of each slice (every reading when nothing is thinned) */
  v: (number | null)[];
  /** lowest and highest reading of each slice: the band around the line */
  lo: (number | null)[];
  hi: (number | null)[];
  source: "full" | "snapshot" | "seconds";
  resolution_s: number;
  events: (CockpitEvent & { historic?: boolean })[];
}

export interface ShiftData extends RangeData {
  shift_start: number;
  insights: InsightStep[];
}

export interface Series {
  sensor: string;
  name: string;
  unit: string;
  t0: number;
  dt: number;
  values: (number | null)[];
  tolerance: Tolerance;
  display?: Display | null;
  limit_word?: string;
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
  display: Display | null;
  limit_word: string;
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

export type ViewData =
  | HistoryData | SpectrumData | DriftData | HistogramData | BoxplotData | ScatterData | BarData | ShiftData
  | IncidentChartData;

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

// -- recordings and incidents (backend recording_api.py, incidents.py) --------

export type IncidentStatus = "open" | "acknowledged";
export type CheckStatus = "found" | "not_found" | "info" | "unknown";
export type LengthUnit = "µm" | "mm" | "m" | "nm";

/** Where the file came from and how it was read. */
export interface RecordingInfo {
  file: string;
  digest: string;
  rows: number;
  duration_s: number;
  source_rate_hz: number | null;
  rate_hz: number;
  part_rpm: number | null;
  metadata: string[];
  analysed_at: string;
}

/** The recording as the engine reports it: in every status message and in recording messages. */
export interface RecordingPublic {
  active: boolean;
  name?: string;
  rate_hz?: number;
  duration_s?: number;
  rows?: number;
  /** server time at which sample 0 of the file started to play */
  started_at?: number;
  hidden_columns?: number;
  incidents: number;
  source?: RecordingInfo;
  slots?: { type: string; fallback: boolean; error: string | null; file: string; column: number | null;
            label: string | null; note?: string }[];
}

export type RecordingMessage = { type: "recording" } & Partial<RecordingPublic>;

export interface AnalysisStats {
  incidents: number;
  shown: number;
  alerts: number;
  seconds_analysed: number;
  evaluations: number;
  elapsed_s: number;
  by_kind: Partial<Record<EventKind, number>>;
}

/** The analysis job and the file's state, from GET /api/recording/status. */
export interface RecordingJob {
  state: "idle" | "running" | "done" | "error";
  progress: number;
  message: string;
  error: string | null;
  result: { stats: AnalysisStats; warnings: string[]; source: RecordingInfo } | null;
  pending: { name: string; digest: string } | null;
  recording: RecordingPublic;
  playbook_error: string | null;
  playbook_checks: number;
}

/** One column of the file, as the upload dialog edits it. */
export interface SensorColumnDraft {
  index: number;
  name: string;
  unit: LengthUnit;
  include: boolean;
  scale: number | null;
  zero: "start" | "none";
  fixture: string;
  tolerance_low: number;
  tolerance_high: number;
  tolerance_source: "placeholder" | "entered";
}

export interface MappingDraft {
  delimiter: ";" | "," | "\t";
  decimal: "." | ",";
  data_line: number;
  time_index: number | null;
  time_unit: "s" | "ms" | "min" | "h";
  rate_hz: number | null;
  part_rpm: number;
  sensors: SensorColumnDraft[];
}

/** What the upload returns: the detected layout, a suggested mapping and the first rows. */
export interface InspectResult {
  file: { name: string; digest: string; bytes: number };
  layout: {
    encoding: string; delimiter: string; decimal: string; data_line: number; header: string[] | null;
    column_count: number; time_column: number | null; time_unit: string; time_unit_guessed: boolean;
    rate_hz: number | null; counter_columns: number[]; data_rows: number; duration_s: number | null;
  };
  mapping: MappingDraft;
  preview: string[][];
  warnings: string[];
}

export interface IncidentRow {
  id: string;
  title: string;
  kind: EventKind;
  kinds: EventKind[];
  severity: Severity;
  status: IncidentStatus;
  t: number;
  t_text: string;
  sensor: string;
  sensor_name: string;
  related: string[];
  repeats: number;
  summary: string;
}

export interface IncidentCheck {
  key: string;
  label: string;
  value: string;
  status: CheckStatus;
  detail: string;
}

export interface IncidentSuggestion {
  text: string;
  reason: string;
  kind: string;
}

export interface IncidentLocation {
  sensor: string;
  sensor_name: string;
  twin_side: "left" | "right" | null;
  /** degrees of turn, counted from the start of the recording; null when the part speed is unknown */
  part_angle_deg: number | null;
  turn: number | null;
  time_of_peak_s: number;
  twin_angle_deg: number | null;
}

export interface IncidentSnapshot {
  t0: number;
  rate_hz: number;
  sensors: { id: string; name: string; unit: string; tolerance: Tolerance; values: (number | null)[] }[];
}

export interface IncidentDetail extends IncidentRow {
  numbers: Record<string, number | string>;
  forecast_min: number | null;
  highlight: { from_s: number; to_s: number };
  window: { from_s: number; to_s: number };
  checks: IncidentCheck[];
  explanation: string[];
  suggestions: IncidentSuggestion[];
  unknown: string[];
  location: IncidentLocation;
  recurrence: { count: number; median_interval_s: number | null };
  snapshot: IncidentSnapshot;
  source: RecordingInfo;
}

export interface IncidentsResponse {
  incidents: IncidentRow[];
  stats: AnalysisStats | null;
  source: RecordingInfo | null;
  warnings: string[];
  recording: RecordingPublic;
}

export interface IncidentSeries {
  sensor: string;
  name: string;
  unit: string;
  tolerance: Tolerance;
  t0: number;
  dt: number;
  values: (number | null)[];
}

/** An incident as the stage draws it: one trace per sensor on the recording's own time axis. */
export interface IncidentChartData {
  view: "incident";
  id: string;
  title: string;
  kind: EventKind;
  severity: Severity;
  sensor: string;
  t: number;
  t_text: string;
  highlight: { from_s: number; to_s: number };
  series: IncidentSeries[];
  insights: InsightStep[];
}

/** Where the twin's spot sits for the selected incident. */
export interface TwinSpot {
  incidentId: string;
  sensor: string;
  /** seconds from the start of the recording */
  t: number;
  severity: Severity;
  /** server time at which sample 0 started to play */
  origin: number;
  /** length of the recording in seconds: the playback loops */
  duration: number;
  /** the part speed the recording was analysed with; null: the spot is shown on the sensor only */
  partRpm: number | null;
}

// -- manuals and recommendations ----------------------------------------------------------

export interface RecommendationSource {
  doc: string;
  page: number | null;
  section: string;
  quote: string;
}

/** One next step. `basis` says where it comes from: a manual page, the measurements alone, or the fixed rules. */
export interface Recommendation {
  id: string;
  action: string;
  why: string;
  basis: "manual" | "data" | "rules";
  facts: string[];
  sources: RecommendationSource[];
}

export interface RecommendationSet {
  /** ai: drafted by the model and checked. rules: the fixed checks, with a note why. none: nothing prepared yet. */
  status: "ai" | "rules" | "none";
  incident: string;
  ai?: boolean;
  model?: string;
  note?: string;
  dropped?: number;
  recommendations?: Recommendation[];
  facts?: { id: string; text: string }[];
  manuals?: number;
  passages?: number;
  seconds?: number;
  generated?: string;
}

export interface ManualDocument {
  name: string;
  kind: "pdf" | "text" | "markdown";
  pages: number;
  passages: number;
  bytes: number;
}

export interface KnowledgeStatus {
  documents: ManualDocument[];
  passages: number;
  warnings: string[];
  digest: string;
  embeddings: { enabled: boolean; model: string; ready: number; error: string | null };
  ai: boolean;
  model: string;
  prefetching: boolean;
  cached: number;
}

export interface ManualPassage {
  uid: string;
  doc: string;
  page: number | null;
  section: string;
  text: string;
  score?: number;
  via?: string;
}

/** What a manual lookup found, shown on screen while the copilot speaks about it. */
export interface ManualHit {
  query: string;
  passages: ManualPassage[];
  message: string;
}

/** A written answer from the manuals: every value cited with section and page. */
export interface ManualSource {
  id: string;
  doc: string;
  doc_title: string;
  section: string;
  section_title: string;
  page: number | null;
  printed_page: number | null;
  citation: string;
  text: string;
  footnotes: string[];
}

export interface ManualAnswer {
  question: string;
  found: boolean;
  answer: string;
  mode: "generated" | "extractive" | "none";
  search: "hybrid" | "keyword";
  sources: ManualSource[];
}

/** One turn of the typed chat: the model's text and the tools it wants run. */
export interface ChatTurn {
  id: string;
  text: string;
  calls: { call_id: string; name: string; arguments: string }[];
}
