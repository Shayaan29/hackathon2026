// Application state (zustand). Live samples live in lib/live.ts instead.
import { create } from "zustand";
import type { SocketState } from "../lib/socket";
import type {
  ChartSpec,
  CockpitConfig,
  CockpitEvent,
  InsightStep,
  KnowledgeStatus,
  ManualHit,
  PartRecord,
  StationState,
  StatusMessage,
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
  | { id: string; t: number; type: "report"; reportId: string; title: string };

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
  toasts: Toast[];
  soundOn: boolean;
  speakAlerts: boolean;
  qrOpen: boolean;
  tokenOk: boolean | null;
  publicUrl: string;
  mission: boolean;
  missionSince: number;
  feed: FeedItem[];
  reportId: string | null;
  /** the part check sheet of this sensor */
  partFor: string | null;
  /** the quality station sheet (part log) of this sensor */
  partsFor: string | null;
  /** the last parts per sensor, newest first */
  partLog: Record<string, PartRecord[]>;
  /** the part that left the sensor last, for a short flash on the tile */
  lastPart: { sensor: string; number: number; verdict: PartRecord["verdict"]; at: number } | null;
  /** the manuals sheet, what is loaded, and the passages of the last answer */
  manualsOpen: boolean;
  knowledge: KnowledgeStatus | null;
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
  addPart: (sensor: string, part: PartRecord | null, station: StationState) => void;
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
  toasts: [],
  soundOn: true,
  speakAlerts: true,
  qrOpen: false,
  tokenOk: null,
  publicUrl: storedPublicUrl(),
  mission: false,
  missionSince: 0,
  feed: [],
  reportId: null,
  partFor: null,
  partsFor: null,
  partLog: {},
  lastPart: null,
  manualsOpen: false,
  knowledge: null,
  manualHit: null,

  patch: (partial) => set(partial),

  addPart: (sensor, part, station) =>
    set((s) => {
      // the status message carries the counters too; this keeps them current between two status messages
      const status = s.status && s.status.sensors[sensor]
        ? { ...s.status, sensors: { ...s.status.sensors, [sensor]: { ...s.status.sensors[sensor], station } } }
        : s.status;
      if (!part) return { status, partLog: { ...s.partLog, [sensor]: [] } };
      const log = (s.partLog[sensor] ?? []).filter((r) => r.number !== part.number);
      return {
        status,
        partLog: { ...s.partLog, [sensor]: [part, ...log].slice(0, 200) },
        lastPart: { sensor, number: part.number, verdict: part.verdict, at: Date.now() },
      };
    }),

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
}));
