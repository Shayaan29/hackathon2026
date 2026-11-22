// Live samples for the sensor tiles. Kept outside React state on purpose:
// 50 updates per second per sensor would re-render the whole cockpit, so
// tiles read from here inside requestAnimationFrame instead.
import type { SamplesMessage } from "../types";

const CAPACITY = 1500; // 30 s at 50 points per second

class LiveSeries {
  readonly t = new Float64Array(CAPACITY);
  readonly v = new Float64Array(CAPACITY);
  count = 0;
  head = 0;

  push(t: number, v: number): void {
    this.t[this.head] = t;
    this.v[this.head] = v;
    this.head = (this.head + 1) % CAPACITY;
    this.count = Math.min(this.count + 1, CAPACITY);
  }

  /** Visit points newer than tMin, oldest first. */
  forEachSince(tMin: number, fn: (t: number, v: number) => void): void {
    for (let i = 0; i < this.count; i++) {
      const idx = (this.head - this.count + i + CAPACITY) % CAPACITY;
      if (this.t[idx] >= tMin) fn(this.t[idx], this.v[idx]);
    }
  }

  latest(): { t: number; v: number } | null {
    if (!this.count) return null;
    const idx = (this.head - 1 + CAPACITY) % CAPACITY;
    return { t: this.t[idx], v: this.v[idx] };
  }
}

const store = new Map<string, LiveSeries>();

export function series(sensor: string): LiveSeries {
  let s = store.get(sensor);
  if (!s) {
    s = new LiveSeries();
    store.set(sensor, s);
  }
  return s;
}

export function ingest(message: SamplesMessage): void {
  const s = series(message.sensor);
  message.values.forEach((value, i) => {
    s.push(message.t0 + i * message.dt, value ?? Number.NaN);
  });
}

/** Voice loudness 0..1 for the orb, written by the voice code every frame. */
export const meters = { level: 0 };

/** Server time minus browser time, so sparklines line up with "now". */
let clockOffset = 0;
export function setServerTime(serverT: number): void {
  clockOffset = serverT - Date.now() / 1000;
}
export function serverNow(): number {
  return Date.now() / 1000 + clockOffset;
}
