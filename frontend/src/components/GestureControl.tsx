// Optional hand gestures (MediaPipe, runs in the browser, no data leaves the laptop).
//   open palm   -> send the current chart back
//   closed fist -> dismiss the current chart
//   thumbs up   -> confirm (a pending setting change, or "show me" on an alert)
// Loaded only when the hand button in the top bar is switched on.
import type { GestureRecognizer } from "@mediapipe/tasks-vision";
import { useEffect, useRef, useState } from "react";
import { controller } from "../controller";
import { useStore } from "../state/store";

const LOCAL_WASM = "/mediapipe/wasm";
const CDN_WASM = "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision/wasm";
const LOCAL_MODEL = "/models/gesture_recognizer.task";
const CDN_MODEL =
  "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task";

const ACTIONS = new Set(["Open_Palm", "Closed_Fist", "Thumb_Up"]);
const HOLD_MS = 600;
const COOLDOWN_MS = 1600;
const LABEL: Record<string, string> = { Open_Palm: "open palm · back", Closed_Fist: "fist · dismiss", Thumb_Up: "thumbs up · confirm" };

async function exists(url: string): Promise<boolean> {
  try {
    const response = await fetch(url, { method: "HEAD" });
    return response.ok && !(response.headers.get("content-type") ?? "").includes("text/html");
  } catch {
    return false;
  }
}

export default function GestureControl() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [state, setState] = useState("starting camera…");
  const [current, setCurrent] = useState<string | null>(null);
  const patch = useStore((s) => s.patch);

  useEffect(() => {
    let recognizer: GestureRecognizer | null = null;
    let stream: MediaStream | null = null;
    let raf = 0;
    let stopped = false;
    let candidate = "";
    let since = 0;
    let lastFired = 0;

    (async () => {
      try {
        const { FilesetResolver, GestureRecognizer } = await import("@mediapipe/tasks-vision");
        setState("loading model…");
        const wasm = (await exists(`${LOCAL_WASM}/vision_wasm_internal.wasm`)) ? LOCAL_WASM : CDN_WASM;
        const model = (await exists(LOCAL_MODEL)) ? LOCAL_MODEL : CDN_MODEL;
        const files = await FilesetResolver.forVisionTasks(wasm);
        recognizer = await GestureRecognizer.createFromOptions(files, {
          baseOptions: { modelAssetPath: model, delegate: "GPU" },
          runningMode: "VIDEO",
          numHands: 1,
        });
        stream = await navigator.mediaDevices.getUserMedia({ video: { width: 640, height: 360 } });
        const video = videoRef.current;
        if (!video || stopped) return;
        video.srcObject = stream;
        await video.play();
        setState("ready");
        const loop = () => {
          if (stopped || !recognizer) return;
          if (video.readyState >= 2) {
            const now = performance.now();
            const result = recognizer.recognizeForVideo(video, now);
            const top = result.gestures[0]?.[0];
            const name = top && top.score > 0.6 && ACTIONS.has(top.categoryName) ? top.categoryName : "";
            setCurrent(name || null);
            if (name !== candidate) {
              candidate = name;
              since = now;
            } else if (name && now - since > HOLD_MS && now - lastFired > COOLDOWN_MS) {
              lastFired = now;
              controller.onGesture(name);
            }
          }
          raf = requestAnimationFrame(loop);
        };
        loop();
      } catch (error) {
        const reason = error instanceof Error
          ? error.message
          : "could not load the gesture model; run npm run fetch:gestures or check the internet connection";
        setState(`gestures unavailable: ${reason}`);
      }
    })();

    return () => {
      stopped = true;
      cancelAnimationFrame(raf);
      stream?.getTracks().forEach((t) => t.stop());
      recognizer?.close();
    };
  }, []);

  return (
    <div className="gesture">
      <video ref={videoRef} muted playsInline />
      <div className="gesture-label">{state === "ready" ? (current ? LABEL[current] : "show a gesture") : state}</div>
      <button className="gesture-off" onClick={() => patch({ gesturesOn: false })} title="Turn gestures off">×</button>
    </div>
  );
}
