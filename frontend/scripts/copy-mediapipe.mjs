// Copies MediaPipe's WebAssembly files into public/ so hand gestures work
// without internet at the venue. Runs automatically after `npm install`.
import { cpSync, existsSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const source = `${root}node_modules/@mediapipe/tasks-vision/wasm`;
const target = `${root}public/mediapipe/wasm`;

if (!existsSync(source)) {
  console.log("copy-mediapipe: @mediapipe/tasks-vision not installed, skipping");
} else {
  mkdirSync(target, { recursive: true });
  cpSync(source, target, { recursive: true });
  console.log("copy-mediapipe: copied gesture runtime to public/mediapipe/wasm");
}
