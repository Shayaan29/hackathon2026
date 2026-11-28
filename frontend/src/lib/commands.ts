// Offline command understanding: the fallback when the AI voice is not
// connected. Covers the pitch in English and German with simple rules,
// so the cockpit stays usable without internet or an API key.
import type { ChartSpec, CockpitEvent, SensorInfo } from "../types";

export type Lang = "en" | "de";

export type Intent =
  | { kind: "show"; spec: ChartSpec }
  | { kind: "stage"; action: "back" | "dismiss" }
  | { kind: "status"; sensor?: string }
  | { kind: "partcheck"; sensor?: string }
  | { kind: "help" }
  | { kind: "unknown" };

export interface CommandContext {
  sensors: SensorInfo[];
  events: CockpitEvent[];
  lastSensor?: string;
}

const GERMAN = /\b(zeig|zeige|letzte|letzten|minuten|sekunden|zurück|wie|ist|der|die|das|ja|nein|bitte|hilfe|alles|teil|bauteil|lerne)\b/;
const NUMBER_WORDS: Record<string, number> = {
  one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, ten: 10, fifteen: 15, twenty: 20, thirty: 30,
  eins: 1, ein: 1, eine: 1, einen: 1, zwei: 2, drei: 3, vier: 4, fünf: 5, sechs: 6, zehn: 10,
  fünfzehn: 15, zwanzig: 20, dreißig: 30,
};
export function detectLang(text: string): Lang {
  return GERMAN.test(text.toLowerCase()) ? "de" : "en";
}

function normalize(text: string): string {
  return text
    .toLowerCase()
    .replace(/[.,!?;:]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function wordsToNumbers(text: string): string {
  return text.replace(/\b[a-zäöüß]+\b/g, (w) => (w in NUMBER_WORDS ? String(NUMBER_WORDS[w]) : w));
}

function findSensor(text: string, sensors: SensorInfo[]): string | undefined {
  for (const s of sensors) {
    if (text.includes(s.name.toLowerCase()) || new RegExp(`\\b${s.id}\\b`).test(text)) return s.id;
  }
  const m = text.match(/\bsensor\s*(\d+)\b/);
  if (m) {
    const byNumber = sensors.find((s) => s.id === `s${m[1]}` || s.name.toLowerCase().endsWith(` ${m[1]}`));
    if (byNumber) return byNumber.id;
  }
  return undefined;
}

function findWindow(text: string): number | undefined {
  const m = text.match(/(\d+(?:\.\d+)?)\s*(s|sec|secs|second|seconds|sekunde|sekunden|min|mins|minute|minutes|minuten|h|hour|hours|stunde|stunden)\b/);
  if (m) {
    const n = Number(m[1]);
    const unit = m[2];
    if (unit.startsWith("h") || unit.startsWith("stunde")) return n * 3600;
    if (unit.startsWith("m")) return n * 60;
    return n;
  }
  if (/\b(last|letzte|letzten) (minute)\b/.test(text)) return 60;
  if (/\b(last|letzte|letzten) (hour|stunde)\b/.test(text)) return 3600;
  return undefined;
}

export function parseCommand(raw: string, ctx: CommandContext): { intent: Intent; lang: Lang } {
  const lang = detectLang(raw);
  const text = wordsToNumbers(normalize(raw));
  const sensor = findSensor(text, ctx.sensors);
  const fallbackSensor = sensor ?? ctx.lastSensor ?? ctx.sensors[0]?.id ?? "s1";
  const has = (re: RegExp) => re.test(text);

  if (has(/\b(help|what can you|hilfe|was kannst)\b/)) return { intent: { kind: "help" }, lang };

  if (has(/\b(back|go back|previous|undo|zurück|vorherige?)\b/)) return { intent: { kind: "stage", action: "back" }, lang };
  if (has(/\b(dismiss|clear|close|hide|remove|weg|schließ\w*|ausblenden|entfern\w*)\b/)) {
    return { intent: { kind: "stage", action: "dismiss" }, lang };
  }

  if (has(/\b(teach|learn|part check|part-check|bauteilprüfung|teile?|lerne?)\b/)
      && has(/\b(part|parts|piece|bauteil\w*|teil\w*)\b/)) {
    return { intent: { kind: "partcheck", sensor }, lang };
  }
  if (has(/\b(status|summary|report|how is|how are|everything ok|all good|zustand|bericht|wie geht|wie steht|wie läuft|alles ok|alles gut)\b/)) {
    return { intent: { kind: "status", sensor }, lang };
  }
  if (has(/\b(show|display|last|live|chart|graph|plot|zeig\w*|letzte\w*|verlauf|diagramm)\b/) || sensor) {
    return { intent: { kind: "show", spec: { view: "timeseries", sensors: [fallbackSensor], window_s: findWindow(text) ?? 300 } }, lang };
  }
  return { intent: { kind: "unknown" }, lang };
}

// ---------------------------------------------------------------------------
// Spoken replies for offline mode
// ---------------------------------------------------------------------------

const PHRASES = {
  show_timeseries: { en: "Here is {name} over the last {window}.", de: "Hier ist {name} über die letzten {window}." },
  back: { en: "Going back.", de: "Zurück." },
  nothing_back: { en: "There is nothing to go back to.", de: "Es gibt nichts, wohin ich zurück kann." },
  dismiss: { en: "Cleared.", de: "Ausgeblendet." },
  help: {
    en: "You can say: show me the last five minutes, teach this part, how many parts, show a bar chart, show the part heights, status, show a histogram, write a report, or go back.",
    de: "Du kannst sagen: zeig die letzten fünf Minuten, lerne dieses Teil, wie viele Teile, zeig ein Balkendiagramm, zeig die Teilehöhen, Status, zeig ein Histogramm, schreib einen Bericht, oder zurück.",
  },
  unknown: {
    en: "Sorry, I did not get that. Say help to hear what I can do.",
    de: "Das habe ich nicht verstanden. Sag Hilfe, dann erkläre ich, was ich kann.",
  },
  partcheck_open: {
    en: "Opening the part check. Hold one good part still under the sensor and press Teach this part.",
    de: "Ich öffne die Bauteilprüfung. Halte ein gutes Teil ruhig unter den Sensor und drücke Teil anlernen.",
  },
  status_ok: { en: "{name} is at {value} micrometres, within tolerance.", de: "{name} steht bei {value} Mikrometer, innerhalb der Toleranz." },
  status_out: { en: "{name} is at {value} micrometres, outside tolerance.", de: "{name} steht bei {value} Mikrometer, außerhalb der Toleranz." },
  status_ok_range: {
    en: "{name} is at {value} millimetres, inside the measuring range.",
    de: "{name} steht bei {value} Millimeter, innerhalb des Messbereichs.",
  },
  status_out_range: {
    en: "{name} is at {value} millimetres, outside the measuring range.",
    de: "{name} steht bei {value} Millimeter, außerhalb des Messbereichs.",
  },
} as const;

export type PhraseKey = keyof typeof PHRASES;

export function phrase(key: PhraseKey, lang: Lang, vars: Record<string, string | number> = {}): string {
  return PHRASES[key][lang].replace(/\{(\w+)\}/g, (_, name: string) => String(vars[name] ?? ""));
}

export function windowWords(seconds: number, lang: Lang): string {
  if (seconds >= 120) {
    const minutes = Math.round(seconds / 60);
    return lang === "de" ? `${minutes} Minuten` : `${minutes} minutes`;
  }
  return lang === "de" ? `${Math.round(seconds)} Sekunden` : `${Math.round(seconds)} seconds`;
}
