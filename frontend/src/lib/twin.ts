// The digital twin: a live 3D model of the measuring setup (three.js).
//
// A granite measuring table with a rotating machined part, an aluminium
// gantry, and one laser triangulation sensor per configured sensor. Each laser
// draws the real triangulation geometry: the emitted beam down to the part and
// the reflected beam back to the receiver lens. Live readings move the laser
// spots, alerts shake the gantry, and mission mode turns the lights down.
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";

export type TwinState = "ok" | "warning" | "critical" | "invalid";

export interface TwinSensor {
  id: string;
  /** half of the tolerance band, used to scale the spot movement and the paint colour */
  range: number;
  nominal: number;
  /** where the laser meets the turning part: angle around the axis and distance from it */
  angle: number;
  radius: number;
}

export interface TwinSource {
  value(id: string): number | null;
  state(id: string): TwinState;
  /** every new reading of a sensor since tMin, oldest first */
  forEachSince(id: string, tMin: number, fn: (t: number, v: number) => void): void;
  /** the server's clock, so the part turns in step with the data */
  now(): number;
}

export { trackFor } from "./tracks";

const PART_R = 0.92;
const PAINT = 512;
const GREEN = [31, 185, 124];
const AMBER = [255, 165, 31];
const RED = [255, 49, 80];

/** Paint colour for a deviation, as a share of the half tolerance band. */
function deviationColor(share: number): string {
  const mix = (a: number[], b: number[], k: number) => a.map((x, i) => Math.round(x + (b[i] - x) * k));
  const c = share < 0.45 ? GREEN : share < 0.8 ? mix(GREEN, AMBER, (share - 0.45) / 0.35)
    : share < 1 ? mix(AMBER, RED, (share - 0.8) / 0.2) : RED;
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

const STATE_COLOR: Record<TwinState, THREE.Color> = {
  ok: new THREE.Color("#6a52ff"),
  warning: new THREE.Color("#ffa51f"),
  critical: new THREE.Color("#ff3150"),
  invalid: new THREE.Color("#6a52ff"),
};

const LOOK = {
  // bloom stays off in the bright room: shiny metal would glow along with the lasers
  light: { bg: new THREE.Color("#e9ecef"), shadow: 0.2, env: 0.75, bloom: 0.0,
    threshold: 1.0, grid: 0.0, glow: 0.35, key: 1.8 },
  dark: { bg: new THREE.Color("#05060f"), shadow: 0.5, env: 0.2, bloom: 1.35,
    threshold: 0.9, grid: 0.45, glow: 0.8, key: 0.7 },
};
// lasers are drawn brighter than white so they, and only they, glow
const HDR = 3.2;

const TABLE_TOP = 0.5;
const PART_TOP = 0.95;
const BEAM_Y = 2.0;
const SENSOR_BOTTOM = BEAM_Y - 0.09 - 0.42;
const SPOT_TRAVEL = 0.07; // scene units for a full half tolerance band

function glowTexture(): THREE.Texture {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d")!;
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(0.25, "rgba(255,255,255,0.55)");
  grad.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, 64, 64);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

/** A beam between two points: a thin bright core and a soft glow around it. */
class Beam {
  readonly group = new THREE.Group();
  private readonly core: THREE.Mesh;
  private readonly halo: THREE.Mesh;
  readonly coreMat: THREE.MeshBasicMaterial;
  readonly haloMat: THREE.MeshBasicMaterial;

  constructor(radius: number, haloRadius: number) {
    this.coreMat = new THREE.MeshBasicMaterial({ color: STATE_COLOR.ok, toneMapped: false, transparent: true });
    this.haloMat = new THREE.MeshBasicMaterial({ color: STATE_COLOR.ok, toneMapped: false, transparent: true,
      opacity: 0.25, blending: THREE.AdditiveBlending, depthWrite: false });
    this.core = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, 1, 8, 1, true), this.coreMat);
    this.halo = new THREE.Mesh(new THREE.CylinderGeometry(haloRadius, haloRadius, 1, 12, 1, true), this.haloMat);
    this.group.add(this.core, this.halo);
  }

  set(from: THREE.Vector3, to: THREE.Vector3): void {
    const dir = new THREE.Vector3().subVectors(to, from);
    const len = dir.length();
    this.group.position.copy(from).addScaledVector(dir, 0.5);
    this.group.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.normalize());
    this.group.scale.set(1, Math.max(len, 1e-4), 1);
  }
}

interface SensorRig {
  cfg: TwinSensor;
  anchor: THREE.Object3D; // where the label points
  emitted: Beam;
  reflected: Beam;
  spot: THREE.Sprite;
  led: THREE.MeshBasicMaterial;
  ring: THREE.MeshBasicMaterial;
  emitter: THREE.Vector3;
  receiver: THREE.Vector3;
  surface: THREE.Vector3;
  shown: number; // smoothed spot offset
  color: THREE.Color;
}

export class Twin {
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.PerspectiveCamera(32, 1, 0.1, 100);
  private readonly controls: OrbitControls;
  private readonly composer: EffectComposer;
  private readonly bloom: UnrealBloomPass;
  private readonly floorMat: THREE.ShadowMaterial;
  private readonly gridMat: THREE.LineBasicMaterial;
  private readonly key: THREE.DirectionalLight;
  private readonly gantry = new THREE.Group();
  private readonly part = new THREE.Group();
  private readonly shock: THREE.Mesh;
  private readonly scan: THREE.Mesh;
  private readonly rigs: SensorRig[] = [];
  private readonly glow = glowTexture();
  private readonly clock = new THREE.Clock();
  private raf = 0;
  private dark = 0; // 0 light .. 1 dark, animated over a fixed time
  private darkFrom = 0;
  private darkTarget = 0;
  private darkAt = -10;
  private knockAt = -10;
  private knockAmp = 0;
  private shockAt = -10;
  private scanAt = -10;
  private lastInteraction = -10;
  private readonly resize: ResizeObserver;
  private paintCtx!: CanvasRenderingContext2D;
  private paintTex!: THREE.CanvasTexture;
  private readonly painted = new Map<string, { t: number; x: number; y: number }>();

  constructor(private readonly host: HTMLElement, sensors: TwinSensor[], private readonly source: TwinSource,
              private readonly omega = 0.9) {
    this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
    this.renderer.toneMapping = THREE.NeutralToneMapping;
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    host.appendChild(this.renderer.domElement);

    const pmrem = new THREE.PMREMGenerator(this.renderer);
    this.scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    this.scene.background = LOOK.light.bg.clone();
    this.scene.fog = new THREE.Fog(LOOK.light.bg.clone(), 14, 30);

    this.camera.position.set(3.4, 3.3, 4.6);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.target.set(0, 1.0, 0);
    this.controls.enableDamping = true;
    this.controls.enablePan = false;
    this.controls.minDistance = 3.5;
    this.controls.maxDistance = 12;
    this.controls.maxPolarAngle = 1.38;
    this.controls.autoRotate = true;
    this.controls.autoRotateSpeed = 0.45;
    this.controls.addEventListener("start", () => {
      this.lastInteraction = this.clock.elapsedTime;
      this.controls.autoRotate = false;
    });

    // light
    this.scene.add(new THREE.HemisphereLight("#ffffff", "#8e98a6", 0.6));
    this.key = new THREE.DirectionalLight("#ffffff", LOOK.light.key);
    this.key.position.set(4, 7, 3);
    this.key.castShadow = true;
    this.key.shadow.mapSize.set(1024, 1024);
    this.key.shadow.camera.left = this.key.shadow.camera.bottom = -4;
    this.key.shadow.camera.right = this.key.shadow.camera.top = 4;
    this.key.shadow.radius = 6;
    this.scene.add(this.key);

    // floor and grid
    // the floor only catches shadows, so there is no horizon line in the bright room
    this.floorMat = new THREE.ShadowMaterial({ color: "#1b2430", opacity: LOOK.light.shadow });
    const floor = new THREE.Mesh(new THREE.CircleGeometry(16, 64), this.floorMat);
    floor.rotation.x = -Math.PI / 2;
    floor.receiveShadow = true;
    this.scene.add(floor);
    const grid = new THREE.GridHelper(24, 48, "#6a52ff", "#6a52ff");
    this.gridMat = grid.material as THREE.LineBasicMaterial;
    this.gridMat.transparent = true;
    this.gridMat.opacity = 0;
    this.gridMat.toneMapped = false;
    grid.position.y = 0.002;
    this.scene.add(grid);

    this.buildMachine(sensors);

    // a ring that runs across the table when something knocks the fixture
    this.shock = new THREE.Mesh(new THREE.RingGeometry(0.96, 1, 96),
      new THREE.MeshBasicMaterial({ color: STATE_COLOR.warning, transparent: true, opacity: 0, toneMapped: false,
        side: THREE.DoubleSide, depthWrite: false, blending: THREE.AdditiveBlending }));
    this.shock.rotation.x = -Math.PI / 2;
    this.shock.position.y = TABLE_TOP + 0.005;
    this.scene.add(this.shock);

    // the scan plane that sweeps the machine once when mission mode starts
    this.scan = new THREE.Mesh(new THREE.PlaneGeometry(6, 4),
      new THREE.MeshBasicMaterial({ color: "#6a52ff", transparent: true, opacity: 0, toneMapped: false,
        side: THREE.DoubleSide, depthWrite: false, blending: THREE.AdditiveBlending }));
    this.scan.rotation.x = -Math.PI / 2;
    this.scene.add(this.scan);

    this.composer = new EffectComposer(this.renderer);
    this.composer.addPass(new RenderPass(this.scene, this.camera));
    this.bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), LOOK.light.bloom, 0.55, LOOK.light.threshold);
    this.composer.addPass(this.bloom);
    this.composer.addPass(new OutputPass());

    this.resize = new ResizeObserver(() => this.fit());
    this.resize.observe(host);
    this.fit();
    this.loop();
  }

  private buildMachine(sensors: TwinSensor[]): void {
    const granite = new THREE.MeshStandardMaterial({ color: "#2b3038", roughness: 0.55, metalness: 0.05 });
    const aluminium = new THREE.MeshStandardMaterial({ color: "#c7ced6", roughness: 0.32, metalness: 0.75 });
    const steel = new THREE.MeshStandardMaterial({ color: "#d9dde2", roughness: 0.18, metalness: 1.0 });
    const housing = new THREE.MeshStandardMaterial({ color: "#3b4149", roughness: 0.45, metalness: 0.4 });

    const add = (geo: THREE.BufferGeometry, mat: THREE.Material, x: number, y: number, z: number,
      parent: THREE.Object3D = this.scene) => {
      const m = new THREE.Mesh(geo, mat);
      m.position.set(x, y, z);
      m.castShadow = m.receiveShadow = true;
      parent.add(m);
      return m;
    };

    // granite measuring table on four feet
    add(new THREE.BoxGeometry(3.8, 0.42, 2.5), granite, 0, TABLE_TOP - 0.21, 0);
    for (const [x, z] of [[-1.6, -1], [1.6, -1], [-1.6, 1], [1.6, 1]]) {
      add(new THREE.CylinderGeometry(0.07, 0.09, 0.08, 16), housing, x, 0.04, z);
    }

    // spindle and the machined part it turns
    add(new THREE.CylinderGeometry(0.16, 0.2, 0.15, 32), housing, 0, TABLE_TOP + 0.075, 0);
    this.part.position.set(0, TABLE_TOP + 0.15, 0);
    this.scene.add(this.part);
    add(new THREE.CylinderGeometry(0.92, 0.92, PART_TOP - TABLE_TOP - 0.15, 96), steel, 0,
      (PART_TOP - TABLE_TOP - 0.15) / 2, 0, this.part);
    // turned grooves and a key slot make the rotation visible
    for (const r of [0.35, 0.62, 0.84]) {
      const groove = new THREE.Mesh(new THREE.TorusGeometry(r, 0.004, 6, 96),
        new THREE.MeshStandardMaterial({ color: "#8d949c", roughness: 0.4, metalness: 1 }));
      groove.rotation.x = Math.PI / 2;
      groove.position.y = PART_TOP - TABLE_TOP - 0.15 + 0.001;
      this.part.add(groove);
    }
    add(new THREE.BoxGeometry(0.22, 0.02, 0.06), housing, 0.75, PART_TOP - TABLE_TOP - 0.15 + 0.004, 0, this.part);

    // the lasers paint what they measure onto the part, so a defect shows where it sits
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = PAINT;
    this.paintCtx = canvas.getContext("2d")!;
    this.paintCtx.lineCap = "round";
    this.paintTex = new THREE.CanvasTexture(canvas);
    this.paintTex.colorSpace = THREE.SRGBColorSpace;
    this.paintTex.anisotropy = 4;
    const paintDisc = new THREE.Mesh(new THREE.CircleGeometry(PART_R - 0.004, 128),
      new THREE.MeshBasicMaterial({ map: this.paintTex, transparent: true, depthWrite: false, toneMapped: false }));
    paintDisc.rotation.x = -Math.PI / 2;
    paintDisc.position.y = PART_TOP - TABLE_TOP - 0.15 + 0.005;
    this.part.add(paintDisc);

    // gantry: two posts and a crossbeam, all of it moves when knocked
    this.scene.add(this.gantry);
    add(new THREE.BoxGeometry(0.2, BEAM_Y - TABLE_TOP + 0.1, 0.24), aluminium, -1.45, (BEAM_Y + TABLE_TOP) / 2, 0, this.gantry);
    add(new THREE.BoxGeometry(0.2, BEAM_Y - TABLE_TOP + 0.1, 0.24), aluminium, 1.45, (BEAM_Y + TABLE_TOP) / 2, 0, this.gantry);
    add(new THREE.BoxGeometry(3.12, 0.18, 0.24), aluminium, 0, BEAM_Y, 0, this.gantry);

    sensors.forEach((cfg) => {
      const x = cfg.angle === Math.PI ? -cfg.radius : cfg.radius;
      const body = add(new THREE.BoxGeometry(0.24, 0.42, 0.2), housing, x + 0.05, SENSOR_BOTTOM + 0.21, 0.0, this.gantry);
      // front window and status LED
      const windowMat = new THREE.MeshStandardMaterial({ color: "#10131a", roughness: 0.05, metalness: 0.2 });
      add(new THREE.BoxGeometry(0.2, 0.012, 0.16), windowMat, 0, -0.21 - 0.006, 0, body);
      const led = new THREE.MeshBasicMaterial({ color: STATE_COLOR.ok, toneMapped: false });
      const ledMesh = new THREE.Mesh(new THREE.SphereGeometry(0.018, 12, 12), led);
      ledMesh.position.set(0.07, 0.14, 0.101);
      body.add(ledMesh);

      const emitted = new Beam(0.006, 0.028);
      const reflected = new Beam(0.003, 0.016);
      reflected.haloMat.opacity = 0.12;
      this.scene.add(emitted.group, reflected.group);

      const spot = new THREE.Sprite(new THREE.SpriteMaterial({ map: this.glow, color: STATE_COLOR.ok, toneMapped: false,
        blending: THREE.AdditiveBlending, depthWrite: false, transparent: true }));
      spot.scale.setScalar(0.16);
      this.scene.add(spot);

      // faint guide for the circle this laser traces on the turning part
      const ring = new THREE.MeshBasicMaterial({ color: "#8a94a3", transparent: true, opacity: 0.25, toneMapped: false,
        side: THREE.DoubleSide, depthWrite: false });
      const radius = cfg.radius;
      const ringMesh = new THREE.Mesh(new THREE.RingGeometry(radius - 0.004, radius + 0.004, 128), ring);
      ringMesh.rotation.x = -Math.PI / 2;
      ringMesh.position.y = PART_TOP + 0.003;
      this.scene.add(ringMesh);

      // sensor cable, routed up over the beam and down the nearest post
      const side = x < 0 ? -1 : 1;
      const cable = new THREE.CatmullRomCurve3([
        new THREE.Vector3(x + 0.05, SENSOR_BOTTOM + 0.42, 0.06),
        new THREE.Vector3(x + 0.05, BEAM_Y + 0.16, 0.14),
        new THREE.Vector3(side * 1.25, BEAM_Y + 0.12, 0.16),
        new THREE.Vector3(side * 1.56, BEAM_Y - 0.2, 0.15),
        new THREE.Vector3(side * 1.57, TABLE_TOP + 0.2, 0.15),
      ]);
      add(new THREE.TubeGeometry(cable, 48, 0.014, 8, false),
        new THREE.MeshStandardMaterial({ color: "#1c2027", roughness: 0.7 }), 0, 0, 0, this.gantry);

      const anchor = new THREE.Object3D();
      anchor.position.set(0, 0.21, 0);
      body.add(anchor);

      this.rigs.push({
        cfg, anchor, emitted, reflected, spot, led, ring,
        emitter: new THREE.Vector3(x, SENSOR_BOTTOM, 0),
        receiver: new THREE.Vector3(x + 0.11, SENSOR_BOTTOM, 0),
        surface: new THREE.Vector3(x, PART_TOP, 0),
        shown: 0,
        color: STATE_COLOR.ok.clone(),
      });
    });
  }

  /** Screen position (CSS pixels inside the host) of each sensor's label anchor. */
  labelPositions(): { id: string; x: number; y: number; visible: boolean }[] {
    const w = this.host.clientWidth;
    const h = this.host.clientHeight;
    const v = new THREE.Vector3();
    return this.rigs.map((rig) => {
      rig.anchor.getWorldPosition(v);
      v.project(this.camera);
      return { id: rig.cfg.id, x: (v.x * 0.5 + 0.5) * w, y: (-v.y * 0.5 + 0.5) * h, visible: v.z < 1 };
    });
  }

  setMission(on: boolean): void {
    this.darkFrom = this.dark;
    this.darkTarget = on ? 1 : 0;
    this.darkAt = this.clock.elapsedTime;
    if (on) this.scanAt = this.clock.elapsedTime + 0.4;
  }

  /** Something hit the fixture: the gantry rings and a shock ring runs over the table. */
  knock(intensity = 1): void {
    this.knockAt = this.clock.elapsedTime;
    this.knockAmp = Math.min(0.012 + 0.018 * intensity, 0.06);
    this.shockAt = this.clock.elapsedTime;
  }

  private fit(): void {
    const w = Math.max(this.host.clientWidth, 1);
    const h = Math.max(this.host.clientHeight, 1);
    this.renderer.setSize(w, h, false);
    this.composer.setSize(w, h);
    this.bloom.resolution.set(w, h);
    this.camera.aspect = w / h;
    // keep the whole machine in view on narrow screens
    this.camera.fov = w / h < 1.2 ? 46 : w / h > 2.6 ? 30 : 36;
    this.camera.updateProjectionMatrix();
  }

  private loop = (): void => {
    this.raf = requestAnimationFrame(this.loop);
    if (document.hidden) return;
    const dt = Math.min(this.clock.getDelta(), 0.05);
    const t = this.clock.elapsedTime;

    // lights: ease between the measuring room and mission mode
    const p = Math.min(Math.max((t - this.darkAt) / 1.6, 0), 1);
    this.dark = this.darkFrom + (this.darkTarget - this.darkFrom) * (p * p * (3 - 2 * p));
    const k = this.dark;
    (this.scene.background as THREE.Color).lerpColors(LOOK.light.bg, LOOK.dark.bg, k);
    (this.scene.fog as THREE.Fog).color.copy(this.scene.background as THREE.Color);
    this.floorMat.opacity = THREE.MathUtils.lerp(LOOK.light.shadow, LOOK.dark.shadow, k);
    this.scene.environmentIntensity = THREE.MathUtils.lerp(LOOK.light.env, LOOK.dark.env, k);
    this.key.intensity = THREE.MathUtils.lerp(LOOK.light.key, LOOK.dark.key, k);
    this.bloom.strength = THREE.MathUtils.lerp(LOOK.light.bloom, LOOK.dark.bloom, k);
    this.bloom.threshold = THREE.MathUtils.lerp(LOOK.light.threshold, LOOK.dark.threshold, k);
    this.gridMat.opacity = THREE.MathUtils.lerp(LOOK.light.grid, LOOK.dark.grid, k);
    const glow = THREE.MathUtils.lerp(LOOK.light.glow, LOOK.dark.glow, k);

    // scan sweep after switching to mission mode
    const scanT = (t - this.scanAt) / 1.8;
    const scanMat = this.scan.material as THREE.MeshBasicMaterial;
    if (scanT >= 0 && scanT <= 1) {
      this.scan.position.y = THREE.MathUtils.lerp(0.02, 2.4, scanT);
      scanMat.opacity = 0.22 * Math.sin(Math.PI * scanT);
    } else {
      scanMat.opacity = 0;
    }

    // the part turns in step with the data; auto-rotation of the camera resumes after a pause
    this.part.rotation.y = (this.omega * this.source.now()) % (2 * Math.PI);
    this.paintTrail(dt);
    if (!this.controls.autoRotate && t - this.lastInteraction > 6) this.controls.autoRotate = true;

    // knock: damped ringing of the gantry, plus the shock ring
    const since = t - this.knockAt;
    const ringing = since < 1.6 ? this.knockAmp * Math.exp(-since * 3.2) * Math.sin(since * 2 * Math.PI * 7.5) : 0;
    this.gantry.position.x = ringing;
    this.gantry.rotation.z = ringing * 0.35;
    const shockT = (t - this.shockAt) / 1.1;
    const shockMat = this.shock.material as THREE.MeshBasicMaterial;
    if (shockT >= 0 && shockT <= 1) {
      this.shock.scale.setScalar(0.2 + 2.4 * shockT);
      shockMat.opacity = 0.85 * (1 - shockT);
    } else {
      shockMat.opacity = 0;
    }

    // lasers follow the live readings
    for (const rig of this.rigs) {
      const state = this.source.state(rig.cfg.id);
      const value = this.source.value(rig.cfg.id);
      const target = value === null || !Number.isFinite(value) ? 0 : THREE.MathUtils.clamp(value / rig.cfg.range, -1.6, 1.6);
      rig.shown += (target - rig.shown) * Math.min(dt * 12, 1);
      rig.color.lerp(STATE_COLOR[state], Math.min(dt * 6, 1));
      const offset = rig.shown * SPOT_TRAVEL;
      const lost = value === null || !Number.isFinite(value);
      const flicker = state === "invalid" ? (Math.sin(t * 40) > 0 ? 1 : 0.05) : 1;

      const emitter = rig.emitter.clone().add(new THREE.Vector3(ringing, 0, 0));
      const receiver = rig.receiver.clone().add(new THREE.Vector3(ringing, 0, 0));
      const surface = rig.surface.clone();
      surface.y = PART_TOP - offset;
      surface.x += ringing * 0.4;
      rig.emitted.set(emitter, surface);
      rig.reflected.set(surface, receiver);
      for (const beam of [rig.emitted, rig.reflected]) {
          beam.coreMat.color.copy(rig.color).multiplyScalar(HDR);
        beam.haloMat.color.copy(rig.color).multiplyScalar(1.4);
        beam.coreMat.opacity = (lost ? 0.25 : 1) * flicker;
      }
      rig.emitted.haloMat.opacity = glow * flicker;
      rig.reflected.haloMat.opacity = glow * 0.45 * flicker;
      rig.spot.position.copy(surface);
      rig.spot.material.color.copy(rig.color).multiplyScalar(HDR);
      rig.spot.material.opacity = flicker;
      rig.spot.scale.setScalar(0.13 + 0.06 * glow + (state === "ok" ? 0 : 0.04 * Math.sin(t * 10)));
      rig.led.color.copy(rig.color).multiplyScalar(HDR);
      rig.ring.opacity = 0.18 + 0.15 * glow;
    }

    this.controls.update();
    this.composer.render(dt);
  };

  /** Paint new readings onto the part at the spot the laser saw them; old paint fades over a few turns. */
  private paintTrail(dt: number): void {
    const ctx = this.paintCtx;
    ctx.globalCompositeOperation = "destination-out";
    ctx.fillStyle = `rgba(0,0,0,${1 - Math.exp(-dt / 28)})`;
    ctx.fillRect(0, 0, PAINT, PAINT);
    ctx.globalCompositeOperation = "source-over";
    if (this.omega <= 0) return;
    const now = this.source.now();
    for (const rig of this.rigs) {
      const { id, angle, radius, nominal, range } = rig.cfg;
      const last = this.painted.get(id);
      this.source.forEachSince(id, last ? last.t + 1e-6 : now - 0.4, (t, v) => {
        const phi = angle - this.omega * t;
        const x = ((radius * Math.cos(phi)) / (2 * PART_R) + 0.5) * PAINT;
        const y = (1 - ((radius * Math.sin(phi)) / (2 * PART_R) + 0.5)) * PAINT;
        const prev = this.painted.get(id);
        if (Number.isFinite(v)) {
          ctx.strokeStyle = deviationColor(Math.abs(v - nominal) / range);
          ctx.lineWidth = 9 + 9 * Math.min(Math.abs(v - nominal) / range, 1.2);
          ctx.beginPath();
          if (prev && Number.isFinite(prev.x) && t - prev.t < 0.4) ctx.moveTo(prev.x, prev.y);
          else ctx.moveTo(x, y);
          ctx.lineTo(x, y);
          ctx.stroke();
          this.painted.set(id, { t, x, y });
        } else {
          this.painted.set(id, { t, x: Number.NaN, y: Number.NaN }); // no reading: leave a gap
        }
      });
    }
    this.paintTex.needsUpdate = true;
  }

  dispose(): void {
    cancelAnimationFrame(this.raf);
    this.resize.disconnect();
    this.controls.dispose();
    this.composer.dispose();
    this.renderer.dispose();
    this.renderer.domElement.remove();
  }
}
