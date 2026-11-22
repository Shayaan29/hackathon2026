// The cockpit's brain on the browser side: routes live data, alerts, voice,
// typed commands, tool calls from the AI, demo controls and gestures.
import { api, ApiError, getToken, initToken, setToken } from "./lib/api";
import { parseCommand, phrase, windowWords, type Intent, type Lang } from "./lib/commands";
import { KIND_LABEL } from "./lib/format";
import { setChartTheme } from "./lib/charts";
import { ingest, meters, setServerTime } from "./lib/live";
import { LiveSocket } from "./lib/socket";
import { alarm, blip, chime, confirmSound, dissolveSound, setSoundEnabled, unlockAudio, whoosh } from "./lib/sound";
import { PushToTalkRecognizer, recognitionAvailable, speak, stopSpeaking } from "./lib/speech";
import { RealtimeVoice } from "./lib/voice";
import { useStore } from "./state/store";
import type { ChartSpec, ChartView, CockpitEvent, ServerMessage, SpectrumData, DriftData } from "./types";

type StageApi = { dissolveFront: () => boolean };

const VIEWS: ChartView[] = ["timeseries", "compare", "spectrum", "drift"];
const store = () => useStore.getState();

class Controller {
  private socket: LiveSocket | null = null;
  private voice: RealtimeVoice | null = null;
  private recognizer: PushToTalkRecognizer | null = null;
  private stageApi: StageApi | null = null;
  private lastSensor: string | undefined;
  private pttActive = false;
  private alertTimer = 0;
  private started = false;

  // -- start-up --------------------------------------------------------

  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;
    initToken();
    try {
      const config = await api.config();
      store().patch({ config });
      if (config.token_required) void this.checkToken();
      else store().patch({ tokenOk: true });
    } catch (error) {
      store().toast(`Backend not reachable: ${String(error)}`, "error");
    }
    this.socket = new LiveSocket("/ws/cockpit", (m) => this.onMessage(m), (s) => store().patch({ socket: s }));
    this.socket.start();
    window.addEventListener("keydown", this.onKeyDown);
    window.addEventListener("keyup", this.onKeyUp);
    window.addEventListener("pointerdown", unlockAudio, { once: true });
    window.setInterval(() => void this.refreshHints(), 8000);
    window.setTimeout(() => void this.refreshHints(), 3000);
  }

  // -- mission mode, reports, setup assistant ---------------------------

  toggleMission(on = !store().mission): void {
    if (on === store().mission) return;
    setChartTheme(on);
    whoosh(!on, 0.9);
    window.clearTimeout(this.missionChartTimer);
    // mission mode starts on the machine; charts fly in when something happens
    if (on) store().patch({ stage: [] });
    store().patch({ mission: on, missionSince: on ? Date.now() : store().missionSince });
    const first = store().config?.sensors[0]?.id;
    if (!on && !store().stage.length && first) this.show({ view: "timeseries", sensors: [first], window_s: 300 }, true);
  }

  private missionChartTimer = 0;

  openReport(eventId?: string): void {
    const id = eventId ?? store().events[store().events.length - 1]?.id;
    if (!id) {
      store().toast("No alert yet, so there is nothing to report.", "info");
      return;
    }
    store().patch({ reportId: id, assistantFor: null });
  }

  openAssistant(sensor?: string): void {
    store().patch({ assistantFor: this.resolveSensor(sensor ?? this.lastSensor ?? this.firstSensor()), reportId: null });
  }

  /** Quietly checks every sensor's setup, so the cockpit can offer a fix on its own. */
  async refreshHints(): Promise<void> {
    const config = store().config;
    if (!config || document.hidden) return;
    const hints = { ...store().hints };
    for (const sensor of config.sensors) {
      try {
        const plan = await api.assistant(sensor.id);
        const before = hints[sensor.id];
        hints[sensor.id] = plan.status;
        if (plan.status === "improve" && before !== "improve") {
          store().pushFeed({ id: `setup-${sensor.id}-${Math.round(plan.t)}`, t: plan.t, type: "setup",
            sensor: sensor.id, text: plan.summary });
        }
      } catch {
        /* backend busy or restarting; try again next round */
      }
    }
    store().patch({ hints });
  }

  registerStage(stageApi: StageApi | null): void {
    this.stageApi = stageApi;
  }

  async checkToken(): Promise<boolean> {
    if (!getToken()) {
      store().patch({ tokenOk: false });
      return false;
    }
    try {
      await api.checkToken();
      store().patch({ tokenOk: true });
      return true;
    } catch {
      store().patch({ tokenOk: false });
      return false;
    }
  }

  async enterToken(): Promise<void> {
    const token = window.prompt("Cockpit token (COCKPIT_TOKEN in backend/.env)");
    if (token === null) return;
    setToken(token);
    const ok = await this.checkToken();
    store().toast(ok ? "Cockpit unlocked" : "Wrong token", ok ? "ok" : "error");
  }

  // -- live data -------------------------------------------------------

  private onMessage(message: ServerMessage): void {
    switch (message.type) {
      case "hello":
        store().patch({ config: message.config, status: message.status });
        setServerTime(message.status.t);
        message.events.forEach((e) => store().addEvent(e));
        if (!store().stage.length) this.show({ view: "timeseries", sensors: [message.config.sensors[0]?.id ?? "s1"], window_s: 300 }, true);
        break;
      case "status":
        setServerTime(message.t);
        store().patch({ status: message });
        break;
      case "samples":
        ingest(message);
        break;
      case "event":
        this.onEvent(message);
        break;
      case "setting_changed":
        store().pushFeed({ id: `set-${message.sensor}-${message.name}-${message.t}`, t: message.t, type: "setting",
          sensor: message.sensor,
          text: `${this.sensorName(message.sensor)}: ${message.name.replace(/_hz$/, "").replace(/_/g, " ")} changed from ${message.old} to ${message.new}` });
        window.setTimeout(() => void this.refreshHints(), 4000);
        break;
      case "shake":
        store().patch({ lastShake: { name: message.name, sensor: message.sensor, t: message.t, intensity: message.intensity } });
        store().pushFeed({ id: `shake-${message.t}`, t: message.t, type: "shake", name: message.name, sensor: message.sensor });
        blip(true);
        break;
      case "report":
        store().pushFeed({ id: `rep-${message.id}-${Date.now()}`, t: Date.now() / 1000, type: "report",
          reportId: message.id, title: message.title });
        store().patch({ reportId: message.id, assistantFor: null });
        break;
      default:
        break;
    }
  }

  private onEvent(event: CockpitEvent): void {
    store().addEvent(event);
    store().patch({ alert: event });
    window.clearTimeout(this.alertTimer);
    if (event.severity !== "critical") {
      this.alertTimer = window.setTimeout(() => {
        if (store().alert?.id === event.id) store().patch({ alert: null });
      }, 15000);
    }
    if (event.severity === "critical") alarm();
    else chime();
    if (store().mission) {
      // hands-free pitch: the matching chart follows the alert, and clears itself later
      window.setTimeout(() => this.showForEvent(event, true), 2600);
      window.clearTimeout(this.missionChartTimer);
      this.missionChartTimer = window.setTimeout(() => {
        if (store().mission) this.dismiss();
      }, 26000);
    }
    if (!store().speakAlerts) return;
    const shake = store().lastShake;
    const byShake = shake && event.kind === "vibration" && Math.abs(event.t - shake.t) < 5 ? shake : null;
    if (this.voice?.connected) {
      this.voice.announce(event, byShake ? `This vibration came right after ${byShake.name} shook their phone.` : undefined);
    } else {
      const text = byShake ? `${byShake.name}, that was you. ${event.summary}` : event.summary;
      // the alert is already in the conversation, so it is only spoken, not repeated
      window.setTimeout(() => this.say(text, "en", false), 450);
    }
  }

  // -- the stage -------------------------------------------------------

  show(spec: ChartSpec, quiet = false): { ok: boolean; view: string; sensors: string[] } {
    const sensors = spec.sensors?.length ? spec.sensors : [this.lastSensor ?? this.firstSensor()];
    const clean: ChartSpec = { ...spec, sensors };
    if (clean.view === "compare" && sensors.length < 2) {
      clean.sensors = (store().config?.sensors ?? []).map((s) => s.id);
    }
    store().pushPanel(clean);
    this.lastSensor = clean.sensors[0];
    if (!quiet) whoosh(false);
    return { ok: true, view: clean.view, sensors: clean.sensors };
  }

  back(): boolean {
    const visible = store().stage.filter((p) => !p.leaving);
    if (visible.length < 2) return false;
    const front = visible[0];
    store().markLeaving(front.id, "forward");
    window.setTimeout(() => store().removePanel(front.id), 750);
    whoosh(true);
    return true;
  }

  dismiss(): boolean {
    const front = store().stage.find((p) => !p.leaving);
    if (!front) return false;
    this.stageApi?.dissolveFront();
    store().markLeaving(front.id, "dust");
    window.setTimeout(() => store().removePanel(front.id), 60);
    dissolveSound();
    return true;
  }

  showForEvent(event: CockpitEvent, keepAlert = false): void {
    if (!keepAlert) store().patch({ alert: null });
    switch (event.kind) {
      case "vibration":
        this.show({ view: "spectrum", sensors: [event.sensor], event_id: event.id });
        break;
      case "drift":
        this.show({ view: "drift", sensors: [event.sensor], window_s: 900 });
        break;
      case "out_of_range":
        this.show({ view: "timeseries", sensors: [event.sensor], window_s: 300 });
        break;
      default:
        this.show({ view: "timeseries", sensors: [event.sensor], window_s: 60 });
    }
  }

  // -- voice: AI mode or offline mode ------------------------------------

  async connectVoice(): Promise<void> {
    unlockAudio();
    const config = store().config;
    if (!config?.voice.enabled) {
      store().toast("AI voice is off (no OPENAI_API_KEY). Using offline voice.", "warn");
      return;
    }
    if (config.token_required && !(await this.checkToken())) {
      await this.enterToken();
      if (!store().tokenOk) return;
    }
    this.voice?.disconnect();
    this.voice = new RealtimeVoice({
      onMode: (mode, error) => {
        store().patch({ voiceMode: mode, voiceError: error ?? null });
        if (mode === "error" && error) store().toast(`Voice: ${error}`, "error");
        if (mode === "ready") store().toast("AI voice connected", "ok");
      },
      onActivity: (activity) => store().patch({ activity }),
      onUserText: (text) => store().addCaption("you", text),
      onAssistantText: (text, final) => store().addCaption("ai", text, final),
      onToolCall: (name, args) => this.runTool(name, args),
      onLevel: (level) => {
        meters.level = level;
      },
      onError: (message) => store().addCaption("system", message),
    });
    try {
      await this.voice.connect();
    } catch (error) {
      console.error(error);
    }
  }

  disconnectVoice(): void {
    this.voice?.disconnect();
    this.voice = null;
    store().patch({ voiceMode: "off", activity: "idle" });
  }

  get aiVoice(): boolean {
    return Boolean(this.voice?.connected);
  }

  pttDown(): void {
    if (this.pttActive) return;
    this.pttActive = true;
    unlockAudio();
    stopSpeaking();
    blip(true);
    if (this.aiVoice) {
      this.voice?.pttStart();
      return;
    }
    if (!recognitionAvailable()) {
      store().toast("This browser has no speech recognition. Type the command instead.", "warn");
      this.pttActive = false;
      return;
    }
    this.recognizer ??= new PushToTalkRecognizer((text) => store().addCaption("you", text, false));
    try {
      this.recognizer.start(this.language());
      store().patch({ activity: "listening" });
    } catch (error) {
      store().toast(String(error), "error");
      this.pttActive = false;
    }
  }

  async pttUp(): Promise<void> {
    if (!this.pttActive) return;
    this.pttActive = false;
    blip(false);
    if (this.aiVoice) {
      this.voice?.pttEnd();
      return;
    }
    if (!this.recognizer) return;
    store().patch({ activity: "thinking" });
    const text = await this.recognizer.stop();
    if (!text) {
      store().patch({ activity: "idle" });
      return;
    }
    store().addCaption("you", text, true);
    await this.handleLocal(text);
  }

  async submitText(text: string): Promise<void> {
    const trimmed = text.trim();
    if (!trimmed) return;
    unlockAudio();
    store().addCaption("you", trimmed, true);
    if (this.aiVoice) {
      this.voice?.sendText(trimmed);
      return;
    }
    await this.handleLocal(trimmed);
  }

  // -- tools the AI calls ----------------------------------------------

  async runTool(name: string, args: Record<string, unknown>): Promise<unknown> {
    const sensor = String(args.sensor ?? this.lastSensor ?? this.firstSensor());
    const windowS = typeof args.window_s === "number" ? args.window_s : undefined;
    switch (name) {
      case "get_data":
        this.lastSensor = sensor;
        return api.getData(sensor, windowS);
      case "analyze":
        this.lastSensor = sensor;
        return api.analyze(sensor, windowS);
      case "show_chart": {
        const view = VIEWS.includes(args.view as ChartView) ? (args.view as ChartView) : "timeseries";
        const sensors = Array.isArray(args.sensors) ? args.sensors.map((s) => this.resolveSensor(String(s))) : [];
        const spec: ChartSpec = { view, sensors, window_s: windowS };
        if (view === "spectrum") spec.event_id = this.latestEvent("vibration", sensors[0])?.id;
        return this.show(spec);
      }
      case "stage_control":
        return args.action === "dismiss" ? { ok: this.dismiss() } : { ok: this.back() };
      case "suggest_settings": {
        this.lastSensor = sensor;
        this.openAssistant(sensor);
        return api.suggestSettings(sensor);
      }
      case "create_report": {
        const eventId = typeof args.event_id === "string" && args.event_id ? args.event_id : undefined;
        return api.createReport(eventId);
      }
      case "change_setting": {
        try {
          const result = await api.changeSetting(sensor, String(args.name), String(args.value), Boolean(args.confirmed));
          if (result.ok) confirmSound();
          return result;
        } catch (error) {
          return { ok: false, error: error instanceof ApiError ? error.message : String(error) };
        }
      }
      default:
        return { ok: false, error: `unknown tool ${name}` };
    }
  }

  // -- offline commands --------------------------------------------------

  async handleLocal(text: string): Promise<void> {
    const config = store().config;
    if (!config) return;
    const lower = text.toLowerCase();
    if (/\b(report|bericht|ticket)\b/.test(lower)) {
      const result = await api.createReport();
      if (!result.ok) this.say(result.error ?? "There is no alert to report yet.");
      else this.say(`The report on ${result.title} is on screen.`);
      return;
    }
    if (/\b(set ?up|optimi[sz]e|einstellung|improve|configure)\b/.test(lower)) {
      const mentioned = config.sensors.find((s) => lower.includes(s.name.toLowerCase()) || lower.includes(s.id));
      this.openAssistant(mentioned?.id);
      return;
    }
    if (/\b(mission|jarvis)\b/.test(lower)) {
      this.toggleMission();
      return;
    }
    const { intent, lang } = parseCommand(text, {
      sensors: config.sensors,
      events: store().events,
      lastSensor: this.lastSensor,
    });
    try {
      await this.execute(intent, lang);
    } catch (error) {
      this.say(String(error instanceof Error ? error.message : error), lang);
    }
  }

  private async execute(intent: Intent, lang: Lang): Promise<void> {
    switch (intent.kind) {
      case "show": {
        const { spec } = intent;
        const result = this.show(spec);
        const name = this.sensorName(result.sensors[0]);
        if (spec.view === "timeseries") {
          this.say(phrase("show_timeseries", lang, { name, window: windowWords(spec.window_s ?? 300, lang) }), lang);
        } else if (spec.view === "compare") {
          this.say(phrase("show_compare", lang), lang);
        } else if (spec.view === "spectrum") {
          const data: SpectrumData = await api.spectrum(result.sensors[0], 2, spec.event_id);
          const clean = data.peak && data.peak.prominence >= 8;
          this.say(clean ? phrase("spectrum_clean", lang, { freq: Math.round(data.peak!.frequency_hz) }) : phrase("spectrum_none", lang), lang);
        } else {
          const data: DriftData = await api.drift(result.sensors[0], spec.window_s ?? 900);
          const minutes = data.forecast?.minutes_to_limit;
          const rate = data.fit?.rate_per_min ?? 0;
          if (minutes !== null && minutes !== undefined && Math.abs(rate) >= 1) {
            this.say(phrase("drift_eta", lang, { name, rate: rate.toFixed(1), minutes: Math.round(minutes) }), lang);
          } else {
            this.say(phrase("drift_none", lang, { name }), lang);
          }
        }
        return;
      }
      case "stage":
        if (intent.action === "back") this.say(phrase(this.back() ? "back" : "nothing_back", lang), lang);
        else if (this.dismiss()) this.say(phrase("dismiss", lang), lang);
        return;
      case "status": {
        const sensors = intent.sensor ? [intent.sensor] : (store().config?.sensors ?? []).map((s) => s.id);
        const parts: string[] = [];
        for (const sid of sensors) {
          const result = (await api.analyze(sid, 60)) as { latest?: number; within_tolerance?: boolean; observations?: string[]; name?: string };
          const name = result.name ?? this.sensorName(sid);
          const value = typeof result.latest === "number" ? result.latest.toFixed(1) : "?";
          parts.push(phrase(result.within_tolerance === false ? "status_out" : "status_ok", lang, { name, value }));
          const notable = (result.observations ?? []).filter((o) => !o.startsWith("Nothing unusual"));
          if (notable.length && lang === "en") parts.push(notable[0]);
        }
        this.say(parts.join(" "), lang);
        return;
      }
      case "set": {
        store().patch({ pending: { ...intent, lang } });
        this.say(phrase("confirm_set", lang, { setting: intent.name.replace(/_/g, " "), name: this.sensorName(intent.sensor), value: intent.value }), lang);
        return;
      }
      case "confirm": {
        const pending = store().pending;
        if (!pending) {
          this.say(phrase("nothing_to_confirm", lang), lang);
          return;
        }
        store().patch({ pending: null });
        try {
          const result = await api.changeSetting(pending.sensor, pending.name, pending.value, true);
          if (result.ok) {
            confirmSound();
            this.say(phrase("set_done", lang, { setting: pending.name.replace(/_/g, " "), name: this.sensorName(pending.sensor), value: String(result.new) }), lang);
          } else {
            this.say(phrase("set_failed", lang, { error: String(result.error) }), lang);
          }
        } catch (error) {
          this.say(phrase("set_failed", lang, { error: error instanceof Error ? error.message : String(error) }), lang);
        }
        return;
      }
      case "cancel":
        store().patch({ pending: null });
        this.say(phrase("cancelled", lang), lang);
        return;
      case "help":
        this.say(phrase("help", lang), lang);
        return;
      default:
        this.say(phrase("unknown", lang), lang);
    }
  }

  /** Caption plus browser speech (offline mode and alert fallback). */
  say(text: string, lang: Lang = "en", caption = true): void {
    if (caption) store().addCaption("ai", text, true);
    if (!store().soundOn) {
      store().patch({ activity: "idle" });
      return;
    }
    store().patch({ activity: "speaking" });
    meters.level = 0.6;
    speak(text, lang, () => {
      meters.level = 0;
      if (store().activity === "speaking") store().patch({ activity: "idle" });
    });
  }

  // -- gestures ----------------------------------------------------------

  onGesture(name: string): void {
    store().patch({ gesture: name });
    window.setTimeout(() => store().patch({ gesture: null }), 1400);
    if (name === "Open_Palm") {
      this.back();
    } else if (name === "Closed_Fist") {
      this.dismiss();
    } else if (name === "Thumb_Up") {
      const alertEvent = store().alert;
      if (store().pending) void this.handleLocal("yes");
      else if (this.aiVoice) this.voice?.sendText("Yes.");
      else if (alertEvent) this.showForEvent(alertEvent);
      confirmSound();
    }
  }

  // -- demo controls -----------------------------------------------------

  async demo(kind: string, body: Record<string, unknown> = {}): Promise<void> {
    try {
      await api.demo(kind, body);
      store().toast(`Demo: ${kind.replace("_", " ")}`, "info");
    } catch (error) {
      const message = error instanceof ApiError && error.status === 401 ? "Locked: enter the cockpit token first" : String(error);
      store().toast(message, "error");
    }
  }

  // -- settings ----------------------------------------------------------

  toggleSound(): void {
    const soundOn = !store().soundOn;
    setSoundEnabled(soundOn);
    if (!soundOn) stopSpeaking();
    store().patch({ soundOn });
  }

  setPublicUrl(url: string): void {
    // keep only the site address: a pasted "/phone" would double up in the QR
    // link, and a "?token=..." must never end up on the judges' phones
    const clean = url.trim().replace(/[?#].*$/, "").replace(/\/+$/, "").replace(/\/phone$/i, "").replace(/\/+$/, "");
    try {
      localStorage.setItem("cockpit-public-url", clean);
    } catch {
      /* private mode */
    }
    store().patch({ publicUrl: clean });
  }

  // -- keyboard ----------------------------------------------------------

  private typing(event: KeyboardEvent): boolean {
    const target = event.target as HTMLElement | null;
    return Boolean(target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable));
  }

  private onKeyDown = (event: KeyboardEvent): void => {
    if (this.typing(event)) return;
    if (event.code === "Space") {
      event.preventDefault();
      if (!event.repeat) this.pttDown();
      return;
    }
    if (event.repeat) return;
    const s = store();
    switch (event.key) {
      case "ArrowLeft":
        this.back();
        break;
      case "Delete":
      case "Backspace":
        this.dismiss();
        break;
      case "d":
      case "D":
        s.patch({ demoOpen: !s.demoOpen });
        break;
      case "q":
      case "Q":
        s.patch({ qrOpen: !s.qrOpen });
        break;
      case "m":
      case "M":
        this.toggleSound();
        break;
      case "Escape":
        if (s.reportId || s.assistantFor) s.patch({ reportId: null, assistantFor: null });
        else s.patch({ qrOpen: false, demoOpen: false, alert: null });
        break;
      case "j":
      case "J":
        this.toggleMission();
        break;
      case "r":
      case "R":
        this.openReport();
        break;
      default:
        if (s.demoOpen) this.demoHotkey(event.key);
    }
  };

  private onKeyUp = (event: KeyboardEvent): void => {
    if (event.code === "Space" && !this.typing(event)) {
      event.preventDefault();
      void this.pttUp();
    }
  };

  private demoHotkey(key: string): void {
    const actions: Record<string, () => void> = {
      "1": () => void this.demo("tap", { sensor: "s1" }),
      "2": () => void this.demo("drift", { sensor: "s2", rate_per_min: 6 }),
      "3": () => void this.demo("drift_stop", { sensor: "s2" }),
      "4": () => void this.demo("noise", { sensor: "s1" }),
      "5": () => void this.demo("dropout", { sensor: "s1" }),
      "6": () => void this.demo("misconfigure", { sensor: "s1" }),
      "0": () => void this.demo("reset"),
    };
    actions[key]?.();
  }

  // -- helpers -----------------------------------------------------------

  private language(): Lang {
    return store().config?.language?.startsWith("de") ? "de" : "en";
  }

  private firstSensor(): string {
    return store().config?.sensors[0]?.id ?? "s1";
  }

  sensorName(id: string): string {
    return store().config?.sensors.find((s) => s.id === id)?.name ?? id;
  }

  private resolveSensor(text: string): string {
    const sensors = store().config?.sensors ?? [];
    const lower = text.toLowerCase().trim();
    const direct = sensors.find((s) => s.id === lower || s.name.toLowerCase() === lower);
    if (direct) return direct.id;
    const digit = lower.match(/\d+/)?.[0];
    return sensors.find((s) => s.id === `s${digit}`)?.id ?? this.firstSensor();
  }

  private latestEvent(kind: CockpitEvent["kind"], sensor?: string): CockpitEvent | undefined {
    const events = store().events;
    for (let i = events.length - 1; i >= 0; i--) {
      const e = events[i];
      if (e.kind === kind && (!sensor || e.sensor === sensor || e.related_sensors.includes(sensor))) return e;
    }
    return undefined;
  }

  eventLabel(event: CockpitEvent): string {
    return `${KIND_LABEL[event.kind]} · ${this.sensorName(event.sensor)}`;
  }
}

export const controller = new Controller();
