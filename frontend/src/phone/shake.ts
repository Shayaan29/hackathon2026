// Shake detection from the phone's motion sensor.
// A shake is three strong jolts within 1.2 seconds; its strength is reported
// so a harder shake knocks the machine harder.

type MotionPermission = { requestPermission?: () => Promise<"granted" | "denied"> };

export type ShakeSupport = "unknown" | "ready" | "denied" | "unsupported";

const JOLT = 13; // m/s² above gravity
const WINDOW_MS = 1200;
const JOLTS = 3;
const COOLDOWN_MS = 3000;

export class ShakeDetector {
  private jolts: number[] = [];
  private peak = 0;
  private lastShake = 0;
  private lastJolt = 0;
  private listening = false;

  constructor(private readonly onShake: (intensity: number) => void) {}

  /** Must run inside a tap: iPhones ask for motion access here. */
  async enable(): Promise<ShakeSupport> {
    if (typeof window === "undefined" || !("DeviceMotionEvent" in window)) return "unsupported";
    const ctor = window.DeviceMotionEvent as unknown as MotionPermission;
    if (typeof ctor.requestPermission === "function") {
      try {
        if ((await ctor.requestPermission()) !== "granted") return "denied";
      } catch {
        return "denied";
      }
    }
    if (!this.listening) {
      window.addEventListener("devicemotion", this.onMotion);
      this.listening = true;
    }
    return "ready";
  }

  disable(): void {
    window.removeEventListener("devicemotion", this.onMotion);
    this.listening = false;
  }

  private onMotion = (event: DeviceMotionEvent): void => {
    const a = event.acceleration;
    let magnitude: number;
    if (a && a.x !== null && a.y !== null && a.z !== null) {
      magnitude = Math.hypot(a.x, a.y, a.z);
    } else {
      const g = event.accelerationIncludingGravity;
      if (!g || g.x === null || g.y === null || g.z === null) return;
      magnitude = Math.abs(Math.hypot(g.x, g.y, g.z) - 9.81);
    }
    const now = performance.now();
    if (magnitude < JOLT || now - this.lastJolt < 90) return;
    this.lastJolt = now;
    this.jolts = [...this.jolts.filter((t) => now - t < WINDOW_MS), now];
    this.peak = Math.max(this.jolts.length === 1 ? 0 : this.peak, magnitude);
    if (this.jolts.length >= JOLTS && now - this.lastShake > COOLDOWN_MS) {
      this.lastShake = now;
      this.jolts = [];
      this.onShake(Math.min(Math.max(this.peak / 20, 0.5), 3));
      this.peak = 0;
    }
  };
}
