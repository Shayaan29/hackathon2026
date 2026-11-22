// Phone flashlight (camera LED) blinking for alerts.
// Works in Chrome on most Android phones. iPhones and some Android browsers do
// not let websites switch the light, so the screen flash stays the fallback.

type TorchConstraint = MediaTrackConstraintSet & { torch?: boolean };
type TorchCapabilities = MediaTrackCapabilities & { torch?: boolean };

export class Torch {
  private track: MediaStreamTrack | null = null;
  private blinking = 0;

  get available(): boolean {
    return this.track !== null && this.track.readyState === "live";
  }

  /** Must run inside a tap (it asks for camera permission). Returns true when the light can be used. */
  async enable(): Promise<boolean> {
    if (this.available) return true;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" } },
        audio: false,
      });
      const track = stream.getVideoTracks()[0];
      const caps = (track?.getCapabilities?.() ?? {}) as TorchCapabilities;
      if (!track || !caps.torch) {
        stream.getTracks().forEach((t) => t.stop());
        return false;
      }
      this.track = track;
      return true;
    } catch {
      return false;
    }
  }

  private async set(on: boolean): Promise<void> {
    try {
      await this.track?.applyConstraints({ advanced: [{ torch: on } as TorchConstraint] });
    } catch {
      /* light busy or released by the system */
    }
  }

  /** Blink the light `times` times; a new blink cancels a running one. */
  async blink(times: number, onMs: number, offMs: number): Promise<void> {
    if (!this.available) return;
    const run = ++this.blinking;
    for (let i = 0; i < times && run === this.blinking; i++) {
      await this.set(true);
      await new Promise((r) => setTimeout(r, onMs));
      await this.set(false);
      await new Promise((r) => setTimeout(r, offMs));
    }
  }

  stop(): void {
    this.blinking++;
    this.track?.stop();
    this.track = null;
  }
}
