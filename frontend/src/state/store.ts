// Application state (zustand). Live samples live in lib/live.ts instead.
import { create } from "zustand";
import type { SocketState } from "../lib/socket";
import type {
  ChartSpec,
  CockpitConfig,
  CockpitEvent,
  IncidentDetail,
  IncidentRow,
  InsightStep,
  KnowledgeStatus,
  ManualHit,
  RecommendationSet,
  StatusMessage,
  TwinSpot,
  VoiceActivity,
  VoiceMode,
} from "../types";

export interface PanelTour {
  steps: InsightStep[];
  /** how many steps are highlighted so far */
  shown: number;
}

export interface StagePanel {
  id: string;
  spec: ChartSpec;
  leaving?: "forward" | "dust";
  /** an explanation being walked through on this chart */
  tour?: PanelTour;
  /** the shift timeline flies to this window; seq changes on every request */
  focus?: { t0: number; t1: number; seq: number };
}

export interface Caption {
  id: number;
  who: "you" | "ai" | "system";
  text: string;
  final: boolean;
}

export interface Toast {
  id: number;
  text: string;
  tone: "info" | "ok" | "warn" | "error";
}

export type FeedItem =
  | { id: string; t: number; type: "alert"; event: CockpitEvent }
  | { id: string; t: number; type: "say"; who: Caption["who"]; text: string; final: boolean }
  | { id: string; t: number; type: "shake"; name: string; sensor: string }
  | { id: string; t: number; type: "setting"; sensor: string; text: string }
  | { id: string; t: number; type: "report"; reportId: string; title: string }
  | { id: string; t: number; type: "setup"; sensor: string; text: string };

export interface ShakeInfo {
  name: string;
  sensor: string;
  t: number;
  intensity: number;
}

export interface PendingChange {
  sensor: string;
  name: string;
  value: string;
  lang: "en" | "de";
}

interface CockpitStore {
  config: CockpitConfig | null;
  socket: SocketState;
  status: StatusMessage | null;
  events: CockpitEvent[];
  alert: CockpitEvent | null;
  stage: StagePanel[];
  voiceMode: VoiceMode;
  voiceError: string | null;
  activity: VoiceActivity;
  captions: Caption[];
  pending: PendingChange | null;
  toasts: Toast[];
  soundOn: boolean;
  speakAlerts: boolean;
  demoOpen: boolean;
  qrOpen: boolean;
  gesturesOn: boolean;
  gesture: string | null;
  tokenOk: boolean | null;
  publicUrl: string;
  mission: boolean;
  missionSince: number;
  feed: FeedItem[];
  reportId: string | null;
  assistantFor: string | null;
  hints: Record<string, "ok" | "improve" | "advice">;
  lastShake: ShakeInfo | null;
  /** the recording sheet and the incident list */
  recordingOpen: boolean;
  incidentsOpen: boolean;
  incidents: IncidentRow[];
  incidentId: string | null;
  incident: IncidentDetail | null;
  /** where the twin shows the selected incident */
  spot: TwinSpot | null;
  /** manuals, and the recommendations per incident ("loading" while the model works) */
  manualsOpen: boolean;
  knowledge: KnowledgeStatus | null;
  recs: Record<string, RecommendationSet | "loading">;
  manualHit: ManualHit | null;

  patch: (partial: Partial<CockpitStore>) => void;
  addEvent: (event: CockpitEvent) => void;
  addCaption: (who: Caption["who"], text: string, final?: boolean) => void;
  pushFeed: (item: FeedItem) => void;
  toast: (text: string, tone?: Toast["tone"]) => void;
  pushPanel: (spec: ChartSpec) => StagePanel;
  markLeaving: (id: string, mode: "forward" | "dust") => void;
  removePanel: (id: string) => void;
  setTour: (id: string, tour: PanelTour | undefined) => void;
  setFocus: (id: string, t0: number, t1: number) => void;
}

let nextId = 1;
const MAX_STACK = 5;

function storedPublicUrl(): string {
  try {
    return localStorage.getItem("cockpit-public-url") ?? "";
  } catch {
    return "";
  }
}

export const useStore = create<CockpitStore>((set, get) => ({
  config: null,
  socket: "connecting",
  status: null,
  events: [],
  alert: null,
  stage: [],
  voiceMode: "off",
  voiceError: null,
  activity: "idle",
  captions: [],
  pending: null,
  toasts: [],
  soundOn: true,
  speakAlerts: true,
  demoOpen: false,
  qrOpen: false,
  gesturesOn: false,
  gesture: null,
  tokenOk: null,
  publicUrl: storedPublicUrl(),
  mission: false,
  missionSince: 0,
  feed: [],
  reportId: null,
  assistantFor: null,
  hints: {},
  lastShake: null,
  recordingOpen: false,
  incidentsOpen: false,
  incidents: [],
  incidentId: null,
  incident: null,
  spot: null,
  manualsOpen: false,
  knowledge: null,
  recs: {},
  manualHit: null,

  patch: (partial) => set(partial),

  addEvent: (event) =>
    set((s) => (s.events.some((e) => e.id === event.id) ? s : {
      events: [...s.events, event].slice(-200),
      feed: [...s.feed, { id: `e-${event.id}`, t: event.t, type: "alert" as const, event }].slice(-80),
    })),

  pushFeed: (item) =>
    set((s) => ({ feed: [...s.feed.filter((f) => f.id !== item.id), item].slice(-80) })),

  addCaption: (who, text, final = true) =>
    set((s) => {
      const captions = [...s.captions];
      const last = captions[captions.length - 1];
      let id: number;
      if (last && last.who === who && !last.final) {
        captions[captions.length - 1] = { ...last, text, final };
        id = last.id;
      } else {
        id = nextId++;
        captions.push({ id, who, text, final });
      }
      const feedId = `c-${id}`;
      const existing = s.feed.find((f) => f.id === feedId);
      const item = { id: feedId, t: existing?.t ?? Date.now() / 1000, type: "say" as const, who, text, final };
      const feed = existing ? s.feed.map((f) => (f.id === feedId ? item : f)) : [...s.feed, item].slice(-80);
      return { captions: captions.slice(-6), feed };
    }),

  toast: (text, tone = "info") => {
    const id = nextId++;
    set((s) => ({ toasts: [...s.toasts, { id, text, tone }] }));
    const ms = tone === "error" ? 9000 : 4500;
    window.setTimeout(() => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })), ms);
  },

  pushPanel: (spec) => {
    const panel: StagePanel = { id: `p${nextId++}`, spec };
    const kept = get().stage.filter((p) => !p.leaving);
    set({ stage: [panel, ...kept].slice(0, MAX_STACK) });
    return panel;
  },

  markLeaving: (id, mode) =>
    set((s) => ({ stage: s.stage.map((p) => (p.id === id ? { ...p, leaving: mode } : p)) })),

  removePanel: (id) => set((s) => ({ stage: s.stage.filter((p) => p.id !== id) })),
  setTour: (id, tour) => set((s) => ({ stage: s.stage.map((p) => (p.id === id ? { ...p, tour } : p)) })),
  setFocus: (id, t0, t1) =>
    set((s) => ({ stage: s.stage.map((p) => (p.id === id ? { ...p, focus: { t0, t1, seq: nextId++ } } : p)) })),
}));
