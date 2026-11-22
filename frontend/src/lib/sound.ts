// Synthesised sound effects: no audio files, works offline.

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

function noise(a: AudioContext, seconds: number): AudioBufferSourceNode {
  const length = Math.floor(a.sampleRate * seconds);
  const buffer = a.createBuffer(1, length, a.sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < length; i++) data[i] = Math.random() * 2 - 1;
  const source = a.createBufferSource();
  source.buffer = buffer;
  return source;
}

function envelope(a: AudioContext, start: number, peak: number, attack: number, release: number): GainNode {
  const gain = a.createGain();
  gain.gain.setValueAtTime(0.0001, start);
  gain.gain.exponentialRampToValueAtTime(peak, start + attack);
  gain.gain.exponentialRampToValueAtTime(0.0001, start + attack + release);
  return gain;
}

function tone(a: AudioContext, freq: number, start: number, duration: number, peak = 0.12, type: OscillatorType = "sine"): void {
  const osc = a.createOscillator();
  const gain = envelope(a, start, peak, 0.015, duration);
  osc.type = type;
  osc.frequency.value = freq;
  osc.connect(gain).connect(a.destination);
  osc.start(start);
  osc.stop(start + duration + 0.05);
}

/** Air rushing past: forward = chart arrives, backward = chart recedes. */
export function whoosh(backward = false, duration = 0.55): void {
  const a = audio();
  if (!a) return;
  const t = a.currentTime;
  const src = noise(a, duration);
  const filter = a.createBiquadFilter();
  filter.type = "bandpass";
  filter.Q.value = 1.1;
  filter.frequency.setValueAtTime(backward ? 300 : 1800, t);
  filter.frequency.exponentialRampToValueAtTime(backward ? 1800 : 260, t + duration);
  const gain = envelope(a, t, 0.14, 0.12, duration - 0.12);
  src.connect(filter).connect(gain).connect(a.destination);
  src.start(t);
  src.stop(t + duration);
}

/** Dust blowing away for the dissolve effect. */
export function dissolveSound(): void {
  const a = audio();
  if (!a) return;
  const t = a.currentTime;
  const src = noise(a, 1.2);
  const filter = a.createBiquadFilter();
  filter.type = "highpass";
  filter.frequency.setValueAtTime(800, t);
  filter.frequency.exponentialRampToValueAtTime(5000, t + 1.1);
  const gain = envelope(a, t, 0.09, 0.05, 1.1);
  src.connect(filter).connect(gain).connect(a.destination);
  src.start(t);
  src.stop(t + 1.2);
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

export function blip(up = true): void {
  const a = audio();
  if (!a) return;
  tone(a, up ? 1200 : 760, a.currentTime, 0.09, 0.08);
}

export function confirmSound(): void {
  const a = audio();
  if (!a) return;
  const t = a.currentTime;
  tone(a, 660, t, 0.18, 0.1);
  tone(a, 990, t + 0.1, 0.3, 0.1);
}
