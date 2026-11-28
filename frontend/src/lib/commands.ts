// Offline command understanding: the fallback when the AI voice is not
// connected. Covers the demo script in English and German with simple rules,
// so the cockpit stays usable without internet or an API key.
import type { ChartSpec, CockpitEvent, SensorInfo } from "../types";

export type Lang = "en" | "de";

export type Intent =
  | { kind: "show"; spec: ChartSpec }
  | { kind: "stage"; action: "back" | "dismiss" }
  | { kind: "status"; sensor?: string }
  | { kind: "partcheck"; sensor?: string }
  | { kind: "set"; sensor: string; name: string; value: string }
  | { kind: "confirm" }
  | { kind: "cancel" }
  | { kind: "help" }
  | { kind: "unknown" };

export interface CommandContext {
  sensors: SensorInfo[];
  events: CockpitEvent[];
  lastSensor?: string;
}

const GERMAN = /\b(zeig|zeige|letzte|letzten|minuten|sekunden|zurück|beide|vergleich\w*|kaputt|defekt|schwingung|wie|ist|der|die|das|ja|nein|bitte|mittelung|messrate|stell|setze|hilfe|alles|sensoren)\b/;
const NUMBER_WORDS: Record<string, number> = {
  one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, ten: 10, fifteen: 15, twenty: 20, thirty: 30,
  eins: 1, ein: 1, eine: 1, einen: 1, zwei: 2, drei: 3, vier: 4, fünf: 5, sechs: 6, zehn: 10,
  fünfzehn: 15, zwanzig: 20, dreißig: 30,
};
const SETTING_WORDS: Record<string, string> = {
  averaging: "averaging", average: "averaging", mittelung: "averaging", mittelwert: "averaging",
  "measuring rate": "measuring_rate_hz", "sample rate": "measuring_rate_hz", messrate: "measuring_rate_hz",
  exposure: "exposure_mode", belichtung: "exposure_mode",
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

function latest(events: CockpitEvent[], kind: CockpitEvent["kind"]): CockpitEvent | undefined {
  for (let i = events.length - 1; i >= 0; i--) if (events[i].kind === kind) return events[i];
  return undefined;
}

export function parseCommand(raw: string, ctx: CommandContext): { intent: Intent; lang: Lang } {
  const lang = detectLang(raw);
  const text = wordsToNumbers(normalize(raw));
  const sensor = findSensor(text, ctx.sensors);
  const fallbackSensor = sensor ?? ctx.lastSensor ?? ctx.sensors[0]?.id ?? "s1";
  const has = (re: RegExp) => re.test(text);

  if (has(/^(yes|yeah|yep|confirm|confirmed|do it|go ahead|ok|okay|ja|jawohl|bestätigt|mach das|mach es)\b/)) {
    return { intent: { kind: "confirm" }, lang };
  }
  if (has(/^(no|nope|cancel|don't|nein|abbrechen|lieber nicht)\b/)) return { intent: { kind: "cancel" }, lang };
  if (has(/\b(help|what can you|hilfe|was kannst)\b/)) return { intent: { kind: "help" }, lang };

  const setMatch = text.match(
    /\b(set|change|increase|raise|lower|reduce|stell\w*|setz\w*|änder\w*|erhöh\w*)\b.*?\b(averaging|average|mittelung|mittelwert|measuring rate|sample rate|messrate|exposure|belichtung)\b/,
  );
  if (setMatch) {
    // The value follows "to"/"auf"; otherwise it is the last number or option
    // in the sentence ("set averaging on sensor 1 64" -> 64, not 1).
    const after = text.match(/\b(?:to|auf)\s+(\d+|standard|intelligent|background)\b/);
    const candidates = text.slice(text.indexOf(setMatch[2])).match(/\b(\d+|standard|intelligent|background)\b/g) ?? [];
    const sensorNumber = text.match(/\bsensor\s*(\d+)\b/)?.[1];
    const value = after?.[1] ?? [...candidates].reverse().find((c) => c !== sensorNumber) ?? candidates[candidates.length - 1];
    if (value) {
      return { intent: { kind: "set", sensor: fallbackSensor, name: SETTING_WORDS[setMatch[2]], value }, lang };
    }
  }

  if (has(/\b(back|go back|previous|undo|zurück|vorherige?)\b/)) return { intent: { kind: "stage", action: "back" }, lang };
  if (has(/\b(dismiss|clear|close|hide|remove|weg|schließ\w*|ausblenden|entfern\w*)\b/)) {
    return { intent: { kind: "stage", action: "dismiss" }, lang };
  }

  if (has(/\b(spectrum|frequenc\w*|fft|vibrat\w*|broken|defect\w*|faulty|kaputt|defekt|frequenz\w*|spektrum|schwingung\w*)\b/)) {
    const ev = latest(ctx.events, "vibration");
    return { intent: { kind: "show", spec: { view: "spectrum", sensors: [sensor ?? ev?.sensor ?? fallbackSensor], event_id: sensor ? undefined : ev?.id } }, lang };
  }
  if (has(/\b(compare|comparison|both|all sensors|side by side|together|vergleich\w*|beide\w*|alle sensoren|zusammen)\b/)) {
    return { intent: { kind: "show", spec: { view: "compare", sensors: ctx.sensors.map((s) => s.id), window_s: findWindow(text) ?? 60 } }, lang };
  }
  if (has(/\b(drift\w*|trend|forecast|predict\w*|tolerance|prognose|toleranz|vorhersage|when will)\b/)) {
    const ev = latest(ctx.events, "drift");
    return { intent: { kind: "show", spec: { view: "drift", sensors: [sensor ?? ev?.sensor ?? fallbackSensor], window_s: 900 } }, lang };
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
  show_compare: { en: "Here are the sensors on one time axis.", de: "Hier sind die Sensoren auf einer Zeitachse." },
  show_spectrum: { en: "Here is the frequency spectrum of {name}.", de: "Hier ist das Frequenzspektrum von {name}." },
  spectrum_clean: {
    en: "Unlikely that the sensor is broken. It is a clean {freq} hertz vibration, which points to something mechanical.",
    de: "Der Sensor ist wahrscheinlich nicht defekt. Es ist eine saubere Schwingung mit {freq} Hertz, das spricht für eine mechanische Ursache.",
  },
  spectrum_none: {
    en: "There is no clear vibration frequency. The signal looks like normal noise.",
    de: "Es gibt keine klare Schwingungsfrequenz. Das Signal sieht nach normalem Rauschen aus.",
  },
  show_drift: { en: "Here is the drift of {name} with the forecast.", de: "Hier ist die Drift von {name} mit Prognose." },
  drift_eta: {
    en: "{name} is drifting {rate} micrometres per minute and reaches the tolerance limit in about {minutes} minutes.",
    de: "{name} driftet {rate} Mikrometer pro Minute und erreicht die Toleranzgrenze in etwa {minutes} Minuten.",
  },
  drift_eta_range: {
    en: "{name} is drifting {rate} micrometres per minute and leaves the measuring range in about {minutes} minutes.",
    de: "{name} driftet {rate} Mikrometer pro Minute und verlässt den Messbereich in etwa {minutes} Minuten.",
  },
  drift_none: { en: "{name} shows no steady drift right now.", de: "{name} zeigt gerade keine gleichmäßige Drift." },
  back: { en: "Going back.", de: "Zurück." },
  nothing_back: { en: "There is nothing to go back to.", de: "Es gibt nichts, wohin ich zurück kann." },
  dismiss: { en: "Cleared.", de: "Ausgeblendet." },
  confirm_set: {
    en: "Shall I set {setting} on {name} to {value}? Say yes to confirm.",
    de: "Soll ich {setting} bei {name} auf {value} setzen? Sag ja zum Bestätigen.",
  },
  set_done: { en: "Done. {setting} on {name} is now {value}.", de: "Erledigt. {setting} bei {name} steht jetzt auf {value}." },
  set_failed: { en: "That did not work: {error}", de: "Das hat nicht geklappt: {error}" },
  cancelled: { en: "Okay, I left it as it is.", de: "Okay, ich lasse es so." },
  nothing_to_confirm: { en: "Nothing is waiting for a yes right now.", de: "Gerade wartet nichts auf eine Bestätigung." },
  help: {
    en: "You can say: show me the last five minutes, is the sensor broken, compare the sensors, show the drift, go back, or status.",
    de: "Du kannst sagen: zeig die letzten fünf Minuten, ist der Sensor kaputt, vergleiche die Sensoren, zeig die Drift, zurück, oder Status.",
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
