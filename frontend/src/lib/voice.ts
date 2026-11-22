// OpenAI Realtime voice over WebRTC with push-to-talk and tool calling.
//
// Flow: the backend mints a short-lived client secret -> the browser opens a
// WebRTC call to OpenAI with it -> configuration goes over the "oai-events"
// data channel -> audio flows over media tracks. The real API key never
// reaches the browser.
import type { CockpitEvent, RealtimeSession, VoiceActivity, VoiceMode } from "../types";
import { api } from "./api";

export interface VoiceCallbacks {
  onMode(mode: VoiceMode, error?: string): void;
  onActivity(activity: VoiceActivity): void;
  onUserText(text: string): void;
  onAssistantText(text: string, final: boolean): void;
  onToolCall(name: string, args: Record<string, unknown>): Promise<unknown>;
  onLevel(level: number): void;
  onError(message: string): void;
}

interface FunctionCallItem {
  type: string;
  name?: string;
  call_id?: string;
  arguments?: string;
}

type ServerEvent = Record<string, unknown> & { type: string };

const IGNORED_ERRORS = ["input_audio_buffer_commit_empty", "response_cancel_not_active"];

export class RealtimeVoice {
  private pc: RTCPeerConnection | null = null;
  private dc: RTCDataChannel | null = null;
  private mic: MediaStream | null = null;
  private micTrack: MediaStreamTrack | null = null;
  private audioEl: HTMLAudioElement | null = null;
  private audioCtx: AudioContext | null = null;
  private outAnalyser: AnalyserNode | null = null;
  private micAnalyser: AnalyserNode | null = null;
  private levelRaf = 0;
  private session: RealtimeSession | null = null;
  private responding = false;
  private talking = false;
  private assistantText = "";
  private announceQueue: string[] = [];
  private releaseTimer = 0;

  constructor(private readonly cb: VoiceCallbacks) {}

  get connected(): boolean {
    return this.dc?.readyState === "open";
  }

  async connect(): Promise<void> {
    this.disconnect();
    this.cb.onMode("connecting");
    // Create the audio context while we are still inside the click that
    // started this, otherwise browsers keep it suspended.
    try {
      this.audioCtx = new AudioContext();
      void this.audioCtx.resume();
    } catch {
      this.audioCtx = null;
    }
    try {
      const session = await api.realtimeSession();
      this.session = session;

      const pc = new RTCPeerConnection();
      this.pc = pc;
      const audioEl = document.createElement("audio");
      audioEl.autoplay = true;
      this.audioEl = audioEl;
      pc.ontrack = (event) => {
        audioEl.srcObject = event.streams[0];
        this.outAnalyser = this.analyser(event.streams[0]);
      };
      pc.onconnectionstatechange = () => {
        if (pc.connectionState === "failed" || pc.connectionState === "disconnected") {
          this.cb.onMode("error", "Voice connection lost");
        }
      };

      const mic = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      this.mic = mic;
      this.micTrack = mic.getAudioTracks()[0];
      this.micTrack.enabled = false; // push-to-talk: muted until the button is held
      pc.addTrack(this.micTrack, mic);
      this.micAnalyser = this.analyser(mic);

      const dc = pc.createDataChannel("oai-events");
      this.dc = dc;
      dc.onopen = () => {
        for (const update of session.session_updates) this.send(update);
        this.cb.onMode("ready");
        this.startLevels();
      };
      dc.onmessage = (event) => this.handle(JSON.parse(event.data as string) as ServerEvent);
      dc.onclose = () => this.cb.onMode("off");

      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      const response = await fetch(session.calls_url, {
        method: "POST",
        body: offer.sdp,
        headers: { Authorization: `Bearer ${session.client_secret}`, "Content-Type": "application/sdp" },
      });
      if (!response.ok) {
        throw new Error(`OpenAI rejected the call (${response.status}): ${await response.text()}`);
      }
      await pc.setRemoteDescription({ type: "answer", sdp: await response.text() });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.disconnect();
      this.cb.onMode("error", message);
      throw error;
    }
  }

  disconnect(): void {
    window.cancelAnimationFrame(this.levelRaf);
    window.clearTimeout(this.releaseTimer);
    if (this.dc) {
      this.dc.onclose = null;
      this.dc.onmessage = null;
    }
    if (this.pc) this.pc.onconnectionstatechange = null;
    this.dc?.close();
    this.pc?.getSenders().forEach((sender) => sender.track?.stop());
    this.pc?.close();
    this.mic?.getTracks().forEach((track) => track.stop());
    if (this.audioEl) this.audioEl.srcObject = null;
    void this.audioCtx?.close();
    this.pc = null;
    this.dc = null;
    this.mic = null;
    this.micTrack = null;
    this.audioCtx = null;
    this.outAnalyser = null;
    this.micAnalyser = null;
    this.responding = false;
    this.talking = false;
    this.announceQueue = [];
  }

  /** Push-to-talk pressed. */
  pttStart(): void {
    if (!this.connected || !this.micTrack) return;
    window.clearTimeout(this.releaseTimer);
    if (this.responding) {
      this.send({ type: "response.cancel" });
      this.send({ type: "output_audio_buffer.clear" });
      this.responding = false;
    }
    this.send({ type: "input_audio_buffer.clear" });
    this.micTrack.enabled = true;
    this.talking = true;
    this.cb.onActivity("listening");
  }

  /** Push-to-talk released: keep the mic open a moment so the last word arrives. */
  pttEnd(): void {
    if (!this.talking) return;
    this.talking = false;
    this.releaseTimer = window.setTimeout(() => {
      if (this.micTrack) this.micTrack.enabled = false;
      this.send({ type: "input_audio_buffer.commit" });
      this.send({ type: "response.create" });
      this.cb.onActivity("thinking");
    }, 300);
  }

  sendText(text: string): void {
    if (!this.connected) return;
    this.send({
      type: "conversation.item.create",
      item: { type: "message", role: "user", content: [{ type: "input_text", text }] },
    });
    this.send({ type: "response.create" });
    this.cb.onActivity("thinking");
  }

  /** Let the copilot speak up about an alert, without interrupting the operator. */
  announce(event: CockpitEvent): void {
    const prefix = this.session?.alert_prefix ?? "[MONITORING ALERT]";
    const facts = JSON.stringify({ sensor: event.sensor, kind: event.kind, severity: event.severity,
      numbers: event.numbers, forecast_minutes: event.forecast_min, also_seen_by: event.related_sensors });
    const text = `${prefix} ${event.summary} Facts: ${facts}`;
    if (this.responding || this.talking) {
      this.announceQueue.push(text);
      return;
    }
    this.sendText(text);
  }

  private send(event: Record<string, unknown>): void {
    if (this.dc?.readyState === "open") this.dc.send(JSON.stringify(event));
  }

  private async handle(event: ServerEvent): Promise<void> {
    switch (event.type) {
      case "response.created":
        this.responding = true;
        this.assistantText = "";
        this.cb.onActivity("thinking");
        break;
      case "response.output_audio_transcript.delta":
      case "response.output_text.delta":
        this.assistantText += String(event.delta ?? "");
        this.cb.onAssistantText(this.assistantText, false);
        this.cb.onActivity("speaking");
        break;
      case "response.output_audio_transcript.done":
      case "response.output_text.done": {
        const text = String(event.transcript ?? event.text ?? this.assistantText);
        if (text) this.cb.onAssistantText(text, true);
        break;
      }
      case "conversation.item.input_audio_transcription.completed":
        if (event.transcript) this.cb.onUserText(String(event.transcript).trim());
        break;
      case "output_audio_buffer.started":
        this.cb.onActivity("speaking");
        break;
      case "output_audio_buffer.stopped":
        if (!this.responding && !this.talking) this.cb.onActivity("idle");
        break;
      case "response.done":
        await this.onResponseDone(event);
        break;
      case "error": {
        const error = (event.error ?? {}) as { message?: string; code?: string; param?: string };
        const optional = `${error.param ?? ""} ${error.message ?? ""}`.includes("transcription");
        if (optional) {
          console.warn("Input transcription not available:", error.message);
        } else if (!IGNORED_ERRORS.includes(error.code ?? "")) {
          this.cb.onError(error.message ?? "Voice error");
        }
        break;
      }
      default:
        break;
    }
  }

  private async onResponseDone(event: ServerEvent): Promise<void> {
    this.responding = false;
    const response = (event.response ?? {}) as { output?: FunctionCallItem[] };
    const calls = (response.output ?? []).filter((item) => item.type === "function_call");
    if (calls.length) {
      for (const call of calls) {
        let args: Record<string, unknown> = {};
        try {
          args = JSON.parse(call.arguments || "{}") as Record<string, unknown>;
        } catch {
          /* keep empty */
        }
        let output: unknown;
        try {
          output = await this.cb.onToolCall(call.name ?? "", args);
        } catch (error) {
          output = { ok: false, error: error instanceof Error ? error.message : String(error) };
        }
        this.send({
          type: "conversation.item.create",
          item: { type: "function_call_output", call_id: call.call_id, output: JSON.stringify(output) },
        });
      }
      this.send({ type: "response.create" });
      return;
    }
    const next = this.announceQueue.shift();
    if (next) {
      this.sendText(next);
    } else if (!this.talking) {
      window.setTimeout(() => {
        if (!this.responding && !this.talking) this.cb.onActivity("idle");
      }, 600);
    }
  }

  private analyser(stream: MediaStream): AnalyserNode | null {
    try {
      this.audioCtx ??= new AudioContext();
      const node = this.audioCtx.createAnalyser();
      node.fftSize = 512;
      this.audioCtx.createMediaStreamSource(stream).connect(node);
      return node;
    } catch {
      return null;
    }
  }

  private startLevels(): void {
    const buffer = new Uint8Array(512);
    const level = (node: AnalyserNode | null): number => {
      if (!node) return 0;
      node.getByteTimeDomainData(buffer);
      let sum = 0;
      for (const value of buffer) sum += ((value - 128) / 128) ** 2;
      return Math.sqrt(sum / buffer.length);
    };
    const loop = () => {
      const value = this.talking ? level(this.micAnalyser) : level(this.outAnalyser);
      this.cb.onLevel(Math.min(1, value * 4));
      this.levelRaf = window.requestAnimationFrame(loop);
    };
    loop();
  }
}
