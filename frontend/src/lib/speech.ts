// Browser text-to-speech and speech recognition: the voice fallback when no
// OpenAI key is set. Edge and Chrome on Windows have good neural voices.

type Lang = "en" | "de";

let preferred: Partial<Record<Lang, SpeechSynthesisVoice>> = {};

function pickVoice(lang: Lang): SpeechSynthesisVoice | undefined {
  if (preferred[lang]) return preferred[lang];
  const voices = window.speechSynthesis?.getVoices() ?? [];
  const matching = voices.filter((v) => v.lang.toLowerCase().startsWith(lang));
  const ranked = matching.sort((a, b) => score(b) - score(a));
  preferred = { ...preferred, [lang]: ranked[0] };
  return ranked[0];
}

function score(voice: SpeechSynthesisVoice): number {
  const name = voice.name.toLowerCase();
  let s = 0;
  if (name.includes("natural") || name.includes("neural")) s += 4;
  if (name.includes("online")) s += 2;
  if (name.includes("google")) s += 2;
  if (name.includes("aria") || name.includes("jenny") || name.includes("katja")) s += 1;
  return s;
}

if (typeof window !== "undefined" && window.speechSynthesis) {
  window.speechSynthesis.onvoiceschanged = () => {
    preferred = {};
  };
}

export function speechAvailable(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
}

export function speak(text: string, lang: Lang = "en", onEnd?: () => void): void {
  if (!speechAvailable()) {
    onEnd?.();
    return;
  }
  const synth = window.speechSynthesis;
  synth.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = lang === "de" ? "de-DE" : "en-US";
  const voice = pickVoice(lang);
  if (voice) utterance.voice = voice;
  utterance.rate = 1.04;
  utterance.onend = () => onEnd?.();
  utterance.onerror = () => onEnd?.();
  synth.speak(utterance);
}

export function stopSpeaking(): void {
  if (speechAvailable()) window.speechSynthesis.cancel();
}

// ---------------------------------------------------------------------------
// Speech recognition (Chrome and Edge; needs internet)
// ---------------------------------------------------------------------------

interface RecognitionResultLike {
  isFinal: boolean;
  0: { transcript: string };
}
interface RecognitionEventLike {
  resultIndex: number;
  results: ArrayLike<RecognitionResultLike>;
}
interface RecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: RecognitionEventLike) => void) | null;
  onend: (() => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}
type RecognitionCtor = new () => RecognitionLike;

function recognitionCtor(): RecognitionCtor | null {
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function recognitionAvailable(): boolean {
  return typeof window !== "undefined" && recognitionCtor() !== null;
}

/** Push-to-talk recognizer: start() on press, stop() on release resolves the text. */
export class PushToTalkRecognizer {
  private rec: RecognitionLike | null = null;
  private finalText = "";
  private interimText = "";
  private resolveStop: ((text: string) => void) | null = null;

  constructor(private readonly onInterim: (text: string) => void) {}

  start(lang: Lang): void {
    const Ctor = recognitionCtor();
    if (!Ctor) throw new Error("Speech recognition is not available in this browser");
    this.rec?.abort();
    this.finalText = "";
    this.interimText = "";
    const rec = new Ctor();
    rec.lang = lang === "de" ? "de-DE" : "en-US";
    rec.continuous = true;
    rec.interimResults = true;
    rec.onresult = (event) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const result = event.results[i];
        if (result.isFinal) this.finalText += `${result[0].transcript} `;
        else interim += result[0].transcript;
      }
      this.interimText = interim;
      this.onInterim(`${this.finalText}${interim}`.trim());
    };
    rec.onerror = () => undefined;
    rec.onend = () => {
      this.resolveStop?.(`${this.finalText}${this.interimText}`.trim());
      this.resolveStop = null;
    };
    this.rec = rec;
    rec.start();
  }

  stop(): Promise<string> {
    return new Promise((resolve) => {
      if (!this.rec) {
        resolve("");
        return;
      }
      this.resolveStop = resolve;
      this.rec.stop();
      window.setTimeout(() => {
        if (this.resolveStop) {
          this.resolveStop(`${this.finalText}${this.interimText}`.trim());
          this.resolveStop = null;
        }
      }, 1500);
    });
  }
}
