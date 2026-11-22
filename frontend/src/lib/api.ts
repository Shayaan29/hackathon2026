// Thin client for the backend REST API.
import type {
  AssistantPlan,
  BarData,
  BoxplotData,
  ChartSpec,
  HistogramData,
  InsightStep,
  RangeData,
  ScatterData,
  ShiftData,
  CockpitConfig,
  DriftData,
  HistoryData,
  RealtimeSession,
  SpectrumData,
  ViewData,
} from "../types";

const TOKEN_KEY = "cockpit-token";

/** Reads ?token=... once, keeps it for the browser session and cleans the URL. */
export function initToken(): void {
  const url = new URL(window.location.href);
  const token = url.searchParams.get("token");
  if (token) {
    sessionStorage.setItem(TOKEN_KEY, token);
    url.searchParams.delete("token");
    window.history.replaceState(null, "", url.toString());
  }
}

export function getToken(): string {
  return sessionStorage.getItem(TOKEN_KEY) ?? "";
}

export function setToken(token: string): void {
  sessionStorage.setItem(TOKEN_KEY, token.trim());
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("X-Cockpit-Token", token);
  if (init.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...init, headers });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* not JSON */
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as T;
}

const post = <T>(path: string, body: unknown = {}) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });

export const api = {
  config: () => request<CockpitConfig>("/api/config"),
  checkToken: () => request<{ ok: boolean }>("/api/auth/check"),
  history: (sensors: string[], windowS: number, maxPoints = 1200) =>
    request<HistoryData>(
      `/api/history?sensors=${encodeURIComponent(sensors.join(","))}&window_s=${windowS}&max_points=${maxPoints}`,
    ),
  spectrum: (sensor: string, windowS = 2, eventId?: string) =>
    request<SpectrumData>(
      `/api/spectrum?sensor=${encodeURIComponent(sensor)}&window_s=${windowS}` +
        (eventId ? `&event_id=${encodeURIComponent(eventId)}` : ""),
    ),
  drift: (sensor: string, windowS = 900, horizonMin = 60) =>
    request<DriftData>(
      `/api/drift?sensor=${encodeURIComponent(sensor)}&window_s=${windowS}&horizon_min=${horizonMin}`,
    ),
  getData: (sensor: string, windowS?: number) =>
    post<Record<string, unknown>>("/api/tools/get_data", { sensor, window_s: windowS }),
  analyze: (sensor: string, windowS?: number) =>
    post<Record<string, unknown>>("/api/tools/analyze", { sensor, window_s: windowS }),
  changeSetting: (sensor: string, name: string, value: string | number, confirmed: boolean) =>
    post<Record<string, unknown>>("/api/tools/change_setting", { sensor, name, value, confirmed }),
  realtimeSession: () => post<RealtimeSession>("/api/realtime/session"),
  explore: (spec: ChartSpec) => {
    const q = new URLSearchParams({ chart: spec.view, sensors: spec.sensors.join(",") });
    if (spec.window_s) q.set("window_s", String(spec.window_s));
    if (spec.group_by) q.set("group_by", spec.group_by);
    if (spec.metric) q.set("metric", spec.metric);
    return request<HistogramData | BoxplotData | ScatterData | BarData>(`/api/explore?${q}`);
  },
  shift: (sensor: string) => request<ShiftData>(`/api/shift?sensor=${encodeURIComponent(sensor)}`),
  range: (sensor: string, t0: number, t1: number, points = 1600) =>
    request<RangeData>(`/api/range?sensor=${encodeURIComponent(sensor)}&t0=${t0}&t1=${t1}&points=${points}`),
  insights: (spec: ChartSpec) => {
    const q = new URLSearchParams({ view: spec.view, sensors: spec.sensors.join(",") });
    if (spec.window_s) q.set("window_s", String(spec.window_s));
    if (spec.event_id) q.set("event_id", spec.event_id);
    return request<{ insights: InsightStep[] }>(`/api/insights?${q}`);
  },
  assistant: (sensor: string) => request<AssistantPlan>(`/api/assistant/${encodeURIComponent(sensor)}`),
  applyAssistant: (sensor: string) =>
    post<{ ok: boolean; error?: string; summary?: string }>(`/api/assistant/${encodeURIComponent(sensor)}/apply`),
  suggestSettings: (sensor: string) => post<Record<string, unknown>>("/api/tools/suggest_settings", { sensor }),
  createReport: (eventId?: string) =>
    post<{ ok: boolean; id?: string; title?: string; error?: string }>("/api/tools/create_report", { event_id: eventId }),
  demo: (kind: string, body: Record<string, unknown> = {}) =>
    post<Record<string, unknown>>(`/api/demo/${kind}`, body),
};

/** Fetches whatever a chart spec needs. */
export function loadView(spec: ChartSpec): Promise<ViewData> {
  const sensors = spec.sensors.length ? spec.sensors : ["s1"];
  switch (spec.view) {
    case "spectrum":
      return api.spectrum(sensors[0], 2, spec.event_id);
    case "drift":
      return api.drift(sensors[0], spec.window_s ?? 900);
    case "compare":
      return api.history(sensors, spec.window_s ?? 60);
    case "histogram":
    case "boxplot":
    case "scatter":
    case "bar":
      return api.explore({ ...spec, sensors });
    case "shift":
      return api.shift(sensors[0]);
    default:
      return api.history([sensors[0]], spec.window_s ?? 300);
  }
}
