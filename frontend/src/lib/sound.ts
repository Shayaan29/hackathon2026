// The only synthesised sounds left: the alert tones of the phone page, which has to get attention in a
// pocket. The cockpit itself makes no effect sounds; its voice is the AI voice, or the browser voice as fallback.

let ctx: AudioContext | null = null;
let enabled = true;

export function setSoundEnabled(on: boolean): void {
  enabled = on;
}

function audio(): AudioContext | null {
  if (!enabled) return null;
  try {
    ctx ??= new AudioContext();
    if (ctx.state === "suspended") void ctx.resume();
    return ctx;
  } catch {
    return null;
  }
}

/** Call from a click or key handler so browsers allow sound later. */
export function unlockAudio(): void {
  const a = audio();
  if (!a) return;
  const buffer = a.createBuffer(1, 1, a.sampleRate);
  const source = a.createBufferSource();
  source.buffer = buffer;
  source.connect(a.destination);
  source.start();
}

function tone(a: AudioContext, freq: number, start: number, duration: number, peak = 0.12, type: OscillatorType = "sine"): void {
  const osc = a.createOscillator();
  const gain = a.createGain();
  gain.gain.setValueAtTime(0.0001, start);
  gain.gain.exponentialRampToValueAtTime(peak, start + 0.015);
  gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.015 + duration);
  osc.type = type;
  osc.frequency.value = freq;
  osc.connect(gain).connect(a.destination);
  osc.start(start);
  osc.stop(start + duration + 0.05);
}

export function chime(): void {
  const a = audio();
  if (!a) return;
  const t = a.currentTime;
  tone(a, 880, t, 0.5);
  tone(a, 1320, t + 0.14, 0.6);
}

export function alarm(): void {
  const a = audio();
  if (!a) return;
  const t = a.currentTime;
  for (let i = 0; i < 3; i++) tone(a, 1046, t + i * 0.22, 0.14, 0.1, "square");
}
