// Downloads MediaPipe's gesture model (about 8 MB) into public/models so the
// optional hand gestures work offline at the venue. Run once: npm run fetch:gestures
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const urls = [
  "https://storage.googleapis.com/mediapipe-models/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task",
  "https://storage.googleapis.com/mediapipe-tasks/gesture_recognizer/gesture_recognizer.task",
];
const dir = fileURLToPath(new URL("../public/models/", import.meta.url));

for (const url of urls) {
  try {
    const response = await fetch(url);
    if (!response.ok) {
      console.warn(`${url}: ${response.status} ${response.statusText}`);
      continue;
    }
    mkdirSync(dir, { recursive: true });
    writeFileSync(`${dir}gesture_recognizer.task`, Buffer.from(await response.arrayBuffer()));
    console.log("Saved public/models/gesture_recognizer.task");
    process.exit(0);
  } catch (error) {
    console.warn(`${url}: ${error}`);
  }
}
console.error("Could not download the gesture model. Gestures will try to load it from the internet.");
process.exit(1);
