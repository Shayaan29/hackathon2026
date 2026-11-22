// WebSocket that reconnects on its own and keeps the connection warm.
import type { ServerMessage } from "../types";

export type SocketState = "connecting" | "open" | "closed";

export class LiveSocket {
  private ws: WebSocket | null = null;
  private retry = 0;
  private pingTimer = 0;
  private reconnectTimer = 0;
  private stopped = false;

  constructor(
    private readonly path: string,
    private readonly onMessage: (message: ServerMessage) => void,
    private readonly onState: (state: SocketState) => void,
  ) {}

  start(): void {
    this.stopped = false;
    this.open();
  }

  /** Sends a text frame if connected; returns whether it went out. */
  send(text: string): boolean {
    if (this.ws?.readyState !== WebSocket.OPEN) return false;
    this.ws.send(text);
    return true;
  }

  stop(): void {
    this.stopped = true;
    window.clearInterval(this.pingTimer);
    window.clearTimeout(this.reconnectTimer);
    this.ws?.close();
  }

  private open(): void {
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    this.onState("connecting");
    const ws = new WebSocket(`${scheme}://${window.location.host}${this.path}`);
    this.ws = ws;
    ws.onopen = () => {
      this.retry = 0;
      this.onState("open");
      window.clearInterval(this.pingTimer);
      this.pingTimer = window.setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send("ping");
      }, 15000);
    };
    ws.onmessage = (event) => {
      try {
        this.onMessage(JSON.parse(event.data as string) as ServerMessage);
      } catch (error) {
        console.error("bad message", error);
      }
    };
    ws.onclose = () => {
      window.clearInterval(this.pingTimer);
      this.onState("closed");
      if (this.stopped) return;
      const delay = Math.min(8000, 500 * 2 ** this.retry++);
      this.reconnectTimer = window.setTimeout(() => this.open(), delay);
    };
    ws.onerror = () => ws.close();
  }
}
