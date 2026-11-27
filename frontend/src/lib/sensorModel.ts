// The optoNCDT 5500 (ILD5500) as a 3D model for the digital twin.
//
// The proportions come from the dimensional drawing in the operating instructions (fig. 5.3 and 5.4): a housing of
// 80 x 60 x 31 mm for all five measuring ranges, two 6 mm mounting bores 70 mm apart, the laser exit and the
// receiver lens 36 mm apart, and the cable on top. The model is stylised, not CAD: surfaces are simplified and it
// carries no brand marks, only the type name of the sensor from the configuration.
//
// It is built in millimetres with the laser exit point at the origin (x towards the receiver, y up, z towards the
// viewer) and scaled by MM, so the twin can place it like any other body.
import * as THREE from "three";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";

/** Scene units per millimetre. The housing plus its holder is 70 mm tall: exactly the gap under the crossbeam. */
export const MM = 0.006;
const HOLDER_TOP = 70;
const CAP_X = 49.9; // the cable leaves the housing here, measured from the laser exit

export interface SensorModel {
  group: THREE.Group;
  /** state LED, driven by the twin like the one of the generic sensor */
  led: THREE.MeshBasicMaterial;
  /** where the callout points: top edge of the housing */
  anchor: THREE.Object3D;
  /** distance from the laser exit to the receiver lens, in scene units */
  baseline: number;
  /** where the cable leaves the sensor, relative to the laser exit, in scene units */
  cableExit: THREE.Vector3;
}

/** True for "optoNCDT ILD5500-50", "ILD 5500-100", "optoNCDT 5500" and the like. */
export function isIld5500(model: string): boolean {
  return /ild\s*-?\s*5500|optoncdt\s*5500/i.test(model);
}

/** "ILD5500-50" out of the configured model name; the whole name when it has no such part. */
export function typeLabel(model: string): string {
  const m = /ild\s*-?\s*(5500)(?:\s*-\s*(\d{2,3}))?/i.exec(model);
  if (!m) return model.trim() || "ILD5500";
  return m[2] ? `ILD${m[1]}-${m[2]}` : `ILD${m[1]}`;
}

function canvasTexture(width: number, height: number, draw: (g: CanvasRenderingContext2D) => void): THREE.CanvasTexture {
  const c = document.createElement("canvas");
  c.width = width;
  c.height = height;
  draw(c.getContext("2d")!);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  tex.anisotropy = 4;
  return tex;
}

/** The standard laser warning label: yellow, a black frame, the radiation symbol and some lines of fine print. */
function warningLabel(): THREE.CanvasTexture {
  return canvasTexture(350, 280, (g) => {
    g.fillStyle = "#101010";
    g.fillRect(0, 0, 350, 280);
    g.fillStyle = "#f4c400";
    g.fillRect(8, 8, 334, 264);
    g.fillStyle = "#101010";
    g.font = "bold 30px Arial, Helvetica, sans-serif";
    g.textAlign = "center";
    g.fillText("LASER RADIATION", 175, 52);
    g.font = "bold 22px Arial, Helvetica, sans-serif";
    g.fillText("DO NOT STARE INTO BEAM", 175, 84);
    g.fillText("CLASS 2 LASER PRODUCT", 175, 112);
    for (let i = 0; i < 4; i++) g.fillRect(34, 140 + i * 17, 282 - (i === 3 ? 90 : 0), 6);
    // radiation symbol: a triangle with a small sun
    g.beginPath();
    g.moveTo(272, 252);
    g.lineTo(316, 252);
    g.lineTo(294, 212);
    g.closePath();
    g.fill();
    g.fillStyle = "#f4c400";
    g.beginPath();
    g.arc(294, 238, 5, 0, Math.PI * 2);
    g.fill();
  });
}

function typePlate(text: string): THREE.CanvasTexture {
  return canvasTexture(512, 128, (g) => {
    g.fillStyle = "#2b3038";
    g.font = "600 76px Arial, Helvetica, sans-serif";
    g.textAlign = "center";
    g.textBaseline = "middle";
    g.fillText(text, 256, 68, 480);
  });
}

export function buildIld5500(model: string): SensorModel {
  const group = new THREE.Group();
  group.scale.setScalar(MM);

  const silver = new THREE.MeshStandardMaterial({ color: "#c9cfd7", roughness: 0.3, metalness: 0.85 });
  const dark = new THREE.MeshStandardMaterial({ color: "#2b2f36", roughness: 0.5, metalness: 0.4 });
  const black = new THREE.MeshStandardMaterial({ color: "#14161a", roughness: 0.6, metalness: 0.2 });
  const glass = new THREE.MeshStandardMaterial({ color: "#3a0d10", roughness: 0.08, metalness: 0.3,
    emissive: "#2a0508", emissiveIntensity: 0.6 });
  const red = new THREE.MeshStandardMaterial({ color: "#c8202a", roughness: 0.55, metalness: 0.05 });
  const flat = (mat: THREE.Material, w: number, h: number, x: number, y: number, z: number, flip = false) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h), mat);
    m.position.set(x, y, z);
    if (flip) m.rotation.y = Math.PI;
    group.add(m);
    return m;
  };
  const solid = (geo: THREE.BufferGeometry, mat: THREE.Material, x: number, y: number, z: number) => {
    const m = new THREE.Mesh(geo, mat);
    m.position.set(x, y, z);
    m.castShadow = m.receiveShadow = true;
    group.add(m);
    return m;
  };

  // housing: dark base with the mounting bores, silver cover above it; x spans -13.1 .. 66.9 around the laser exit
  const cx = 26.9;
  solid(new RoundedBoxGeometry(80, 11, 31, 3, 1.2), dark, cx, 5.5, 0);
  solid(new RoundedBoxGeometry(80, 49, 31, 3, 1.2), silver, cx, 35.5, 0);

  const front = 15.55;
  for (const x of [-8.1, 61.9]) { // the two 6 mm bores, 70 mm apart
    const ring = new THREE.Mesh(new THREE.RingGeometry(3, 4.2, 28), new THREE.MeshStandardMaterial({ color: "#8d949d",
      roughness: 0.3, metalness: 0.9 }));
    ring.position.set(x, 6, front);
    const hole = new THREE.Mesh(new THREE.CircleGeometry(3, 28), black);
    hole.position.set(x, 6, front - 0.02);
    group.add(ring, hole);
  }
  flat(glass, 7, 6.5, 0, 5.5, front).name = "emitter window";           // laser exit
  flat(glass, 20, 7, 36.4, 5.5, front).name = "receiver lens";          // receiver, 36.4 mm from the laser exit

  const label = flat(new THREE.MeshStandardMaterial({ map: warningLabel(), roughness: 0.6 }), 35, 28, 11.4, 43, front);
  label.name = "laser label";
  flat(new THREE.MeshBasicMaterial({ map: typePlate(typeLabel(model)), transparent: true, toneMapped: false }), 38, 9.5, 8, 21,
    front);

  // state LED, seen from the front as a small dot and from behind where the real sensor has it
  const led = new THREE.MeshBasicMaterial({ color: "#6a52ff", toneMapped: false });
  const dotFront = new THREE.Mesh(new THREE.SphereGeometry(1.7, 14, 14), led);
  dotFront.position.set(54, 53, front);
  group.add(dotFront);

  // rear: red cap on top, black panel with the two LEDs and the two keys
  const rear = -15.55;
  solid(new RoundedBoxGeometry(62, 20, 3, 3, 1), red, cx, 50, rear - 0.2);
  solid(new THREE.BoxGeometry(62, 26, 1), black, cx, 27, rear);
  const dotRear = new THREE.Mesh(new THREE.SphereGeometry(1.7, 14, 14), led);
  dotRear.position.set(cx + 14, 33, rear - 0.9);
  group.add(dotRear);
  const outputLed = new THREE.Mesh(new THREE.SphereGeometry(1.7, 14, 14),
    new THREE.MeshBasicMaterial({ color: new THREE.Color("#1fb97c").multiplyScalar(2.4), toneMapped: false }));
  outputLed.position.set(cx + 14, 27, rear - 0.9);
  group.add(outputLed);
  for (const y of [22, 14]) {
    const key = new THREE.Mesh(new THREE.CylinderGeometry(4, 4, 1.2, 24), dark);
    key.rotation.x = Math.PI / 2;
    key.position.set(cx - 6, y, rear - 0.9);
    group.add(key);
  }

  // holder between the housing and the crossbeam, and the cable gland next to it
  solid(new RoundedBoxGeometry(53, HOLDER_TOP - 60, 31, 2, 1), new THREE.MeshStandardMaterial({ color: "#aeb6c0",
    roughness: 0.35, metalness: 0.8 }), 13.4, (60 + HOLDER_TOP) / 2, 0);
  solid(new THREE.CylinderGeometry(3.6, 3.6, 8, 20), black, CAP_X, 64, 0);

  const anchor = new THREE.Object3D();
  anchor.position.set(cx, 60, 0);
  group.add(anchor);

  return {
    group, led, anchor,
    baseline: 36.4 * MM,
    cableExit: new THREE.Vector3(CAP_X * MM, 68 * MM, 0),
  };
}
