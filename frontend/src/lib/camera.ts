// The camera next to the sensor (a webcam or a USB camera on the cockpit laptop): a live preview and a JPEG
// snapshot of each part while it rests under the laser.

const KEY = "cockpit-camera";

export class Camera {
  readonly video: HTMLVideoElement;
  private stream: MediaStream | null = null;

  constructor() {
    this.video = document.createElement("video");
    this.video.muted = true;
    this.video.playsInline = true;
  }

  get on(): boolean {
    return this.stream !== null;
  }

  /** The cameras the browser can see (names appear after the first permission). */
  static async list(): Promise<MediaDeviceInfo[]> {
    if (!navigator.mediaDevices?.enumerateDevices) return [];
    return (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput");
  }

  static saved(): string | null {
    try {
      return localStorage.getItem(KEY);
    } catch {
      return null;
    }
  }

  async start(deviceId?: string | null): Promise<string> {
    this.stop();
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("This browser cannot use a camera here (it needs localhost or https).");
    const video: MediaTrackConstraints = { width: { ideal: 1280 }, height: { ideal: 720 } };
    if (deviceId) video.deviceId = { exact: deviceId };
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video, audio: false });
    } catch (error) {
      if (!deviceId) throw error;
      stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });  // the saved camera is gone
    }
    this.stream = stream;
    this.video.srcObject = stream;
    await this.video.play().catch(() => undefined);
    const id = stream.getVideoTracks()[0]?.getSettings().deviceId ?? "";
    try {
      if (id) localStorage.setItem(KEY, id);
    } catch {
      /* private mode */
    }
    return stream.getVideoTracks()[0]?.label || "camera";
  }

  stop(): void {
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.video.srcObject = null;
  }

  /** The current frame as a JPEG, at most 960 px wide. */
  async snapshot(): Promise<Blob | null> {
    const v = this.video;
    if (!this.stream || !v.videoWidth) return null;
    const scale = Math.min(1, 960 / v.videoWidth);
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(v.videoWidth * scale);
    canvas.height = Math.round(v.videoHeight * scale);
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
    // the time in the corner, as on an inspection photo
    ctx.font = `${Math.round(canvas.height / 24)}px sans-serif`;
    ctx.fillStyle = "rgba(0,0,0,0.55)";
    const stamp = new Date().toLocaleTimeString();
    const w = ctx.measureText(stamp).width + 16;
    ctx.fillRect(canvas.width - w - 8, canvas.height - canvas.height / 14 - 8, w, canvas.height / 14);
    ctx.fillStyle = "#fff";
    ctx.fillText(stamp, canvas.width - w, canvas.height - 8 - canvas.height / 60);
    return new Promise((resolve) => canvas.toBlob((b) => resolve(b), "image/jpeg", 0.82));
  }
}

export const camera = new Camera();
