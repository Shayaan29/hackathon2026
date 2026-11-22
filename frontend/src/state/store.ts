// Application state (zustand). Live samples live in lib/live.ts instead.
import { create } from "zustand";
import type { SocketState } from "../lib/socket";
import type {
  ChartSpec,
  CockpitConfig,
  CockpitEvent,
  StatusMessage,
  VoiceActivity,
  VoiceMode,
} from "../types";

export interface StagePanel {
  id: string;
  spec: ChartSpec;
  leaving?: "forward" | "dust";
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

  patch: (partial: Partial<CockpitStore>) => void;
  addEvent: (event: CockpitEvent) => void;
  addCaption: (who: Caption["who"], text: string, final?: boolean) => void;
  toast: (text: string, tone?: Toast["tone"]) => void;
  pushPanel: (spec: ChartSpec) => StagePanel;
  markLeaving: (id: string, mode: "forward" | "dust") => void;
  removePanel: (id: string) => void;
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

  patch: (partial) => set(partial),

  addEvent: (event) =>
    set((s) => (s.events.some((e) => e.id === event.id) ? s : { events: [...s.events, event].slice(-200) })),

  addCaption: (who, text, final = true) =>
    set((s) => {
      const captions = [...s.captions];
      const last = captions[captions.length - 1];
      if (last && last.who === who && !last.final) {
        captions[captions.length - 1] = { ...last, text, final };
      } else {
        captions.push({ id: nextId++, who, text, final });
      }
      return { captions: captions.slice(-6) };
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
}));
