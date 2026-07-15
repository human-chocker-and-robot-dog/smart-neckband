import {
  ECG_SAMPLE_RATE_HZ,
  LiveEcgBatch,
  PublicStatus,
  ServerEventSchema,
  Snapshot,
  SNAPSHOT_SECONDS
} from "../lib/protocol.js";
import { DedupeState, reconnectDelayMs, shouldAcceptLiveBatch, shouldLoadPublicLiveSession } from "./live-client.js";
import {
  BackgroundChoice,
  EcgPointBuffer,
  appendEcgBatch,
  beatIntervalMsFromHr,
  pulseFromBeatAge,
  smoothEcgBuffer,
  solidBackgroundChoice
} from "./live-rendering.js";
import "./styles.css";

const MAX_SAMPLES = ECG_SAMPLE_RATE_HZ * SNAPSHOT_SECONDS;
const SETTINGS_KEY = "smart-collar-xinsu-adapter:v2";
const WALLPAPERS = Array.from({ length: 10 }, (_, index) => `/xinsu/images/bg${index + 1}.jpg`);

type ConnectionState = "missing_config" | "connecting" | "live" | "reconnecting" | "closed";
type AudioType = "standard" | "digital" | "ambient" | "lowfreq" | "custom" | "none";

type ThreeRuntime = {
  initThree(containerId: string, currentStyle: string): void;
  updateThree(time: number, pulseIntensity: number, effectIntensity?: number, effectSpeed?: number): void;
  renderThree(): void;
  onWindowResize(): void;
  resetParticles(style: string): void;
  updateBackgroundColor(color: string): void;
  setParticleEffect(effect: string): void;
  setParticleColor(color: string): void;
  setParticleCount(count: number): void;
  setParticleSize(size: number): void;
  setEffectHeartRateConfig(config: Record<string, unknown>): void;
  setParticlesEnabled(enabled: boolean): void;
};

type UiSettings = {
  background: BackgroundChoice;
  accentColor: string;
  particleColor: string;
  particleEffect: string;
  particleCount: number;
  particleSize: number;
  particlesEnabled: boolean;
  bindEffectsToHeartRate: boolean;
  effectIntensity: number;
  effectSpeed: number;
  ecgGridOpacity: number;
  ecgGridColor: string;
  ecgLineColor: string;
  ecgLineWidth: number;
  ecgLineHeight: number;
  audioEnabled: boolean;
  audioType: AudioType;
  audioVolume: number;
  audioFrequency: number;
};

const defaultSettings: UiSettings = {
  background: solidBackgroundChoice("#000000"),
  accentColor: "#ff0033",
  particleColor: "#ffffff",
  particleEffect: "原始星空",
  particleCount: 2000,
  particleSize: 0.5,
  particlesEnabled: true,
  bindEffectsToHeartRate: true,
  effectIntensity: 1,
  effectSpeed: 1,
  ecgGridOpacity: 0,
  ecgGridColor: "#ffff00",
  ecgLineColor: "#ff0033",
  ecgLineWidth: 3,
  ecgLineHeight: 0.5,
  audioEnabled: false,
  audioType: "standard",
  audioVolume: 70,
  audioFrequency: 667
};

const params = new URLSearchParams(window.location.search);
let sessionId = params.get("session_id") ?? "";
let viewerToken = params.get("viewer_token") ?? "";
const publicLiveMode = shouldLoadPublicLiveSession(window.location.pathname, sessionId, viewerToken);
const dedupe: DedupeState = { lastSeq: -1, lastTimestampMs: 0 };

let settings = loadSettings();
let three: ThreeRuntime | null = null;
let waveform: EcgPointBuffer = { samples: [], rPeaks: [] };
let displayedWaveform: EcgPointBuffer = { samples: [], rPeaks: [] };
let status: PublicStatus | null = null;
let connection: ConnectionState = sessionId && viewerToken ? "connecting" : publicLiveMode ? "connecting" : "missing_config";
let lastSeq: number | null = null;
let lastBeatAtMs = -Infinity;
let socket: WebSocket | null = null;
let reconnectTimer: number | null = null;
let closed = false;
let attempt = 0;
let beatAnimationTimer: number | null = null;
let nextEstimatedBeatAtMs = Number.POSITIVE_INFINITY;
let audioContext: AudioContext | null = null;
let audioGain: GainNode | null = null;

function byId<T extends HTMLElement>(id: string): T | null {
  return document.getElementById(id) as T | null;
}

function inputValue(event: Event): string {
  return (event.currentTarget as HTMLInputElement).value;
}

function inputChecked(event: Event): boolean {
  return (event.currentTarget as HTMLInputElement).checked;
}

function inputFile(event: Event): File | null {
  return (event.currentTarget as HTMLInputElement).files?.[0] ?? null;
}

function colorToRgbParts(color: string): string {
  const match = /^#([0-9a-fA-F]{6})$/.exec(color);
  if (!match) {
    return "255, 0, 51";
  }
  const value = Number.parseInt(match[1], 16);
  return `${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function cleanString(value: unknown, fallback: string): string {
  return typeof value === "string" && value.length > 0 ? value : fallback;
}

function cleanNumber(value: unknown, fallback: number, min: number, max: number): number {
  return typeof value === "number" && Number.isFinite(value) ? Math.min(max, Math.max(min, value)) : fallback;
}

function cleanBoolean(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function cleanAudioType(value: unknown, fallback: AudioType): AudioType {
  return value === "standard" || value === "digital" || value === "ambient" || value === "lowfreq" || value === "custom" || value === "none"
    ? value
    : fallback;
}

function loadSettings(): UiSettings {
  try {
    const raw = window.localStorage.getItem(SETTINGS_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    if (!isRecord(parsed)) {
      return { ...defaultSettings, background: { ...defaultSettings.background } };
    }
    const backgroundRecord = isRecord(parsed.background) ? parsed.background : {};
    const backgroundType = backgroundRecord.type === "image" ? "image" : "color";
    return {
      background: {
        type: backgroundType,
        color: cleanString(backgroundRecord.color, defaultSettings.background.color),
        image: cleanString(backgroundRecord.image, "")
      },
      accentColor: cleanString(parsed.accentColor, defaultSettings.accentColor),
      particleColor: cleanString(parsed.particleColor, defaultSettings.particleColor),
      particleEffect: cleanString(parsed.particleEffect, defaultSettings.particleEffect),
      particleCount: Math.round(cleanNumber(parsed.particleCount, defaultSettings.particleCount, 500, 5000)),
      particleSize: cleanNumber(parsed.particleSize, defaultSettings.particleSize, 0.1, 2),
      particlesEnabled: cleanBoolean(parsed.particlesEnabled, defaultSettings.particlesEnabled),
      bindEffectsToHeartRate: cleanBoolean(parsed.bindEffectsToHeartRate, defaultSettings.bindEffectsToHeartRate),
      effectIntensity: cleanNumber(parsed.effectIntensity, defaultSettings.effectIntensity, 0.1, 2),
      effectSpeed: cleanNumber(parsed.effectSpeed, defaultSettings.effectSpeed, 0.2, 3),
      ecgGridOpacity: cleanNumber(parsed.ecgGridOpacity, defaultSettings.ecgGridOpacity, 0, 1),
      ecgGridColor: cleanString(parsed.ecgGridColor, defaultSettings.ecgGridColor),
      ecgLineColor: cleanString(parsed.ecgLineColor, defaultSettings.ecgLineColor),
      ecgLineWidth: cleanNumber(parsed.ecgLineWidth, defaultSettings.ecgLineWidth, 1, 5),
      ecgLineHeight: cleanNumber(parsed.ecgLineHeight, defaultSettings.ecgLineHeight, 0.1, 1),
      audioEnabled: cleanBoolean(parsed.audioEnabled, defaultSettings.audioEnabled),
      audioType: cleanAudioType(parsed.audioType, defaultSettings.audioType),
      audioVolume: cleanNumber(parsed.audioVolume, defaultSettings.audioVolume, 0, 100),
      audioFrequency: Math.round(cleanNumber(parsed.audioFrequency, defaultSettings.audioFrequency, 200, 1000))
    };
  } catch {
    return { ...defaultSettings, background: { ...defaultSettings.background } };
  }
}

function saveSettings(): void {
  try {
    window.localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    // Visual settings are best effort and never security-sensitive.
  }
}

function statusLabel(): string {
  if (connection === "missing_config") return "缺少 viewer_url";
  if (connection === "connecting") return "连接中";
  if (connection === "reconnecting") return "重连中";
  if (connection === "closed") return "已停止";
  if (!status) return "等待数据";
  if (status.state === "signal_lost") return "信号丢失";
  if (status.state === "stale") return "数据延迟";
  if (status.state === "offline") return "离线";
  return "实时";
}

function currentBpmText(): string {
  return status?.hr_bpm == null ? "--" : status.hr_bpm.toFixed(0);
}

function applyBranding(): void {
  document.body.classList.add("smart-collar-live");
  const logo = document.querySelector<HTMLElement>(".logo");
  if (logo) logo.textContent = "Live Beta";

  const firstVisitTitle = document.querySelector<HTMLElement>(".first-visit-header h3");
  if (firstVisitTitle) firstVisitTitle.textContent = "AI Smart Collar Live Beta";
  const bluetoothCheck = byId("bluetoothCheckResult");
  if (bluetoothCheck) {
    bluetoothCheck.innerHTML = `
      <div class="check-status success">
        <p>使用管理员 API 创建 session 后，通过 viewer_url 打开本页。</p>
        <div class="bluetooth-info">
          <h4>Live Beta</h4>
          <p>Windows uploader 上传 NeuroKit2 clean ECG、R 峰、HR、SQI 和 lead-off；此页面只负责实时可视化。</p>
        </div>
      </div>
    `;
  }

  const warning = document.querySelector<HTMLElement>(".warning-info");
  if (warning) {
    warning.textContent = "AI Smart Collar V0 Live Beta · 实时可视化，不用于医疗诊断";
  }
  const footerInfo = document.querySelector<HTMLElement>(".footer-info");
  if (footerInfo) {
    footerInfo.innerHTML = `
      <div class="live-meta">
        <span id="sessionMeta">Session ${sessionId || "--"}</span>
        <span id="liveSeqMeta">Seq --</span>
        <span id="liveQualityMeta">SQI --</span>
      </div>
    `;
  }
  const connectBtn = byId<HTMLButtonElement>("connectBtn");
  if (connectBtn) {
    connectBtn.textContent = sessionId && viewerToken ? "重新连接 Live" : "缺少 viewer_url";
    connectBtn.addEventListener("click", () => {
      if (!sessionId || !viewerToken) {
        updateError("请使用 /api/session 返回的 viewer_url 打开页面");
        return;
      }
      reconnectNow();
    });
  }
  const soundLabel = document.querySelector<HTMLElement>(".sound-label");
  if (soundLabel) soundLabel.textContent = "音效";
}

function updateStatusUi(): void {
  const sessionMeta = byId("sessionMeta");
  if (sessionMeta) sessionMeta.textContent = `Session ${sessionId || "--"}`;
  const bpmDisplay = byId("bpm-display");
  if (bpmDisplay) bpmDisplay.textContent = currentBpmText();
  const statusText = byId("statusText");
  if (statusText) statusText.textContent = statusLabel();
  const statusDot = byId("statusDot");
  if (statusDot) {
    statusDot.classList.toggle("connected", connection === "live" && status?.state === "live");
    statusDot.classList.toggle("alarm", status?.lead_off === true || status?.state === "signal_lost");
  }
  const connectBtn = byId<HTMLButtonElement>("connectBtn");
  if (connectBtn) {
    connectBtn.textContent = sessionId && viewerToken ? "重新连接 Live" : publicLiveMode ? "重新连接 Live" : "缺少 viewer_url";
    connectBtn.classList.toggle("connected", connection === "live");
    connectBtn.disabled = false;
  }
  const seqMeta = byId("liveSeqMeta");
  if (seqMeta) seqMeta.textContent = `Seq ${lastSeq ?? "--"}`;
  const qualityMeta = byId("liveQualityMeta");
  if (qualityMeta) {
    const sqi = status?.sqi == null ? "--" : status.sqi.toFixed(2);
    const lead = status?.lead_off ? "LEAD OFF" : "LEAD OK";
    qualityMeta.textContent = `SQI ${sqi} · ${lead}`;
  }
}

function updateError(message: string): void {
  const qualityMeta = byId("liveQualityMeta");
  if (qualityMeta) qualityMeta.textContent = message;
}

function applyBackground(choice = settings.background): void {
  const colorGroup = document.querySelector<HTMLElement>(".bg-color-group");
  const imageGroup = document.querySelector<HTMLElement>(".bg-image-group");
  const colorRadio = document.querySelector<HTMLInputElement>('input[name="bg-type"][value="color"]');
  const imageRadio = document.querySelector<HTMLInputElement>('input[name="bg-type"][value="image"]');
  if (colorRadio) colorRadio.checked = choice.type === "color";
  if (imageRadio) imageRadio.checked = choice.type === "image";
  if (colorGroup) colorGroup.style.display = choice.type === "color" ? "block" : "none";
  if (imageGroup) imageGroup.style.display = choice.type === "image" ? "block" : "none";

  if (choice.type === "image" && choice.image) {
    document.body.style.backgroundColor = choice.color;
    document.body.style.backgroundImage = `url("${choice.image}")`;
    document.body.style.backgroundPosition = "center";
    document.body.style.backgroundSize = "cover";
    document.body.style.backgroundRepeat = "no-repeat";
  } else {
    document.body.style.background = choice.color;
    document.body.style.backgroundImage = "none";
    document.body.style.backgroundColor = choice.color;
  }
  three?.updateBackgroundColor(choice.color);
}

function applyAccent(color = settings.accentColor): void {
  document.body.style.setProperty("--accent", color);
  document.body.style.setProperty("--accent-rgb", colorToRgbParts(color));
}

function setupSettingsPanel(): void {
  const panel = byId("settings-panel");
  byId("settingsBtn")?.addEventListener("click", () => panel?.classList.add("show"));
  byId("closeSettingsBtn")?.addEventListener("click", () => panel?.classList.remove("show"));
  byId("resetSettingsBtn")?.addEventListener("click", () => {
    settings = { ...defaultSettings, background: { ...defaultSettings.background } };
    syncControlsFromSettings();
    saveSettings();
  });
  byId("resetAllSettingsBtn")?.addEventListener("click", () => {
    settings = { ...defaultSettings, background: { ...defaultSettings.background } };
    syncControlsFromSettings();
    saveSettings();
  });

  document.querySelectorAll<HTMLButtonElement>(".settings-nav-item").forEach((item) => {
    item.addEventListener("click", () => {
      const section = item.dataset.section;
      document.querySelectorAll(".settings-nav-item").forEach((nav) => nav.classList.remove("active"));
      item.classList.add("active");
      document.querySelectorAll<HTMLElement>(".settings-section").forEach((settingsSection) => {
        settingsSection.style.display = settingsSection.id === `${section}-section` ? "block" : "none";
      });
    });
  });
}

function setupBackgroundControls(): void {
  document.querySelectorAll<HTMLInputElement>('input[name="bg-type"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      settings.background = radio.value === "image" ? { ...settings.background, type: "image" } : solidBackgroundChoice(settings.background.color);
      applyBackground();
      saveSettings();
    });
  });
  byId<HTMLInputElement>("bgColorPicker")?.addEventListener("input", (event) => {
    const value = inputValue(event);
    settings.background = { ...settings.background, type: "color", color: value };
    applyBackground();
    saveSettings();
  });
  byId<HTMLInputElement>("bgImageUrl")?.addEventListener("change", (event) => {
    const image = inputValue(event).trim();
    if (!image) return;
    settings.background = { ...settings.background, type: "image", image };
    applyBackground();
    saveSettings();
  });
  byId<HTMLInputElement>("bgImageUpload")?.addEventListener("change", (event) => {
    const file = inputFile(event);
    if (!file) return;
    const reader = new FileReader();
    reader.addEventListener("load", () => {
      if (typeof reader.result !== "string") return;
      settings.background = { ...settings.background, type: "image", image: reader.result };
      applyBackground();
      saveSettings();
    });
    reader.readAsDataURL(file);
  });

  const wallpaperGrid = byId("wallpaperGrid");
  if (wallpaperGrid) {
    wallpaperGrid.innerHTML = "";
    WALLPAPERS.forEach((wallpaper, index) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "wallpaper-item";
      item.title = `背景 ${index + 1}`;
      const img = document.createElement("img");
      img.src = wallpaper;
      img.alt = `背景 ${index + 1}`;
      item.appendChild(img);
      item.addEventListener("click", () => {
        settings.background = { ...settings.background, type: "image", image: wallpaper };
        applyBackground();
        saveSettings();
        document.querySelectorAll(".wallpaper-item").forEach((wallpaperItem) => wallpaperItem.classList.remove("selected"));
        item.classList.add("selected");
      });
      wallpaperGrid.appendChild(item);
    });
  }
}

function setupThemeControls(): void {
  document.querySelectorAll<HTMLInputElement>('input[name="theme-mode"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      document.body.classList.remove("light-mode", "dark-mode", "auto-mode");
      document.body.classList.add(`${radio.value}-mode`);
      applyBackground();
    });
  });
  byId<HTMLInputElement>("themeColor")?.addEventListener("input", (event) => {
    settings.accentColor = inputValue(event);
    applyAccent();
    saveSettings();
  });
  document.querySelectorAll<HTMLButtonElement>(".color-preset").forEach((button) => {
    button.addEventListener("click", () => {
      const color = button.dataset.color;
      if (!color) return;
      settings.accentColor = color;
      const input = byId<HTMLInputElement>("themeColor");
      if (input) input.value = color;
      applyAccent();
      saveSettings();
    });
  });
  byId<HTMLInputElement>("footerToggle")?.addEventListener("change", (event) => {
    const footer = document.querySelector<HTMLElement>(".footer");
    if (footer) footer.style.display = inputChecked(event) ? "block" : "none";
  });
}

function setupParticleControls(): void {
  byId<HTMLInputElement>("particlesToggle")?.addEventListener("change", (event) => {
    settings.particlesEnabled = inputChecked(event);
    three?.setParticlesEnabled(settings.particlesEnabled);
    saveSettings();
  });
  byId<HTMLSelectElement>("particlesEffect")?.addEventListener("change", (event) => {
    settings.particleEffect = inputValue(event);
    three?.setParticleEffect(settings.particleEffect);
    saveSettings();
  });
  byId<HTMLInputElement>("particlesColor")?.addEventListener("input", (event) => {
    settings.particleColor = inputValue(event);
    three?.setParticleColor(settings.particleColor);
    saveSettings();
  });
  const particleCount = byId<HTMLInputElement>("particlesCount");
  const particleCountInput = byId<HTMLInputElement>("particlesCountInput");
  const setParticleCount = (value: number) => {
    settings.particleCount = value;
    if (particleCount) particleCount.value = String(value);
    if (particleCountInput) particleCountInput.value = String(value);
    three?.setParticleCount(value);
    saveSettings();
  };
  particleCount?.addEventListener("input", (event) => setParticleCount(Number(inputValue(event))));
  particleCountInput?.addEventListener("input", (event) => setParticleCount(Number(inputValue(event))));

  const particleSize = byId<HTMLInputElement>("particlesSize");
  const particleSizeInput = byId<HTMLInputElement>("particlesSizeInput");
  const setParticleSize = (value: number) => {
    settings.particleSize = value;
    if (particleSize) particleSize.value = String(value);
    if (particleSizeInput) particleSizeInput.value = String(value);
    three?.setParticleSize(value);
    saveSettings();
  };
  particleSize?.addEventListener("input", (event) => setParticleSize(Number(inputValue(event))));
  particleSizeInput?.addEventListener("input", (event) => setParticleSize(Number(inputValue(event))));

  byId<HTMLInputElement>("effectHeartRateToggle")?.addEventListener("change", (event) => {
    settings.bindEffectsToHeartRate = inputChecked(event);
    saveSettings();
  });
  const intensity = byId<HTMLInputElement>("effectHeartRateIntensity");
  const intensityInput = byId<HTMLInputElement>("effectHeartRateIntensityInput");
  const setIntensity = (value: number) => {
    settings.effectIntensity = value;
    if (intensity) intensity.value = String(value);
    if (intensityInput) intensityInput.value = String(value);
    saveSettings();
  };
  intensity?.addEventListener("input", (event) => setIntensity(Number(inputValue(event))));
  intensityInput?.addEventListener("input", (event) => setIntensity(Number(inputValue(event))));
}

function setupHeartRateControls(): void {
  document.querySelectorAll<HTMLInputElement>('input[name="hr-position"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      const section = document.querySelector<HTMLElement>(".heart-rate-section");
      section?.classList.remove("position-left", "position-right", "position-center");
      section?.classList.add(`position-${radio.value}`);
    });
  });
  document.querySelectorAll<HTMLInputElement>('input[name="hr-style"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      const display = document.querySelector<HTMLElement>(".heart-rate-display");
      display?.classList.remove(
        "style-digital",
        "style-analog",
        "style-3d",
        "style-pulse",
        "style-neon",
        "style-retro",
        "style-futuristic",
        "style-minimal",
        "style-diode"
      );
      display?.classList.add(`style-${radio.value}`);
    });
  });
  byId<HTMLInputElement>("heartRateColor")?.addEventListener("input", (event) => {
    document.body.style.setProperty("--heart-rate-color", inputValue(event));
  });
  byId<HTMLInputElement>("heartRateSize")?.addEventListener("input", (event) => {
    const display = byId("bpm-display");
    const size = inputValue(event);
    if (display) display.style.fontSize = `${size}px`;
    const value = byId("heartRateSizeInput");
    if (value instanceof HTMLInputElement) value.value = size;
  });
}

function setupEcgControls(): void {
  byId<HTMLInputElement>("ecgGridOpacity")?.addEventListener("input", (event) => {
    settings.ecgGridOpacity = Number(inputValue(event));
    const value = byId("ecgGridOpacityValue");
    if (value) value.textContent = settings.ecgGridOpacity.toFixed(2);
    applyGridVisibility();
    saveSettings();
  });
  byId<HTMLInputElement>("ecgGridColor")?.addEventListener("input", (event) => {
    settings.ecgGridColor = inputValue(event);
    saveSettings();
  });
  byId<HTMLInputElement>("ecgLineColor")?.addEventListener("input", (event) => {
    settings.ecgLineColor = inputValue(event);
    saveSettings();
  });
  byId<HTMLInputElement>("ecgLineWidth")?.addEventListener("input", (event) => {
    settings.ecgLineWidth = Number(inputValue(event));
    const value = byId("ecgLineWidthValue");
    if (value) value.textContent = String(settings.ecgLineWidth);
    saveSettings();
  });
  byId<HTMLInputElement>("ecgLineHeight")?.addEventListener("input", (event) => {
    settings.ecgLineHeight = Number(inputValue(event));
    const value = byId("ecgLineHeightValue");
    if (value) value.textContent = settings.ecgLineHeight.toFixed(2);
    saveSettings();
  });
  byId("ecgRandomBtn")?.addEventListener("click", () => {
    updateError("实时 ECG 使用 uploader 数据，模拟参数不会覆盖正式波形");
  });
}

function ensureAudioContext(): boolean {
  if (audioContext && audioGain) {
    return true;
  }
  const audioWindow = window as Window & { webkitAudioContext?: typeof AudioContext };
  const AudioCtor = window.AudioContext ?? audioWindow.webkitAudioContext;
  if (!AudioCtor) {
    updateError("当前浏览器不支持 Web Audio");
    return false;
  }
  audioContext = new AudioCtor();
  audioGain = audioContext.createGain();
  audioGain.gain.value = settings.audioVolume / 100;
  audioGain.connect(audioContext.destination);
  return true;
}

function setAudioEnabled(enabled: boolean): void {
  settings.audioEnabled = enabled;
  const headerToggle = byId<HTMLInputElement>("audioToggle");
  const settingsToggle = byId<HTMLInputElement>("audioToggleSetting");
  if (headerToggle) headerToggle.checked = enabled;
  if (settingsToggle) settingsToggle.checked = enabled;
  if (enabled && ensureAudioContext()) {
    void audioContext?.resume().then(() => playHeartbeatAudio({ force: true }));
  }
  saveSettings();
}

function audioProfile(type: AudioType): { oscillator: OscillatorType; frequency: number; attack: number; sustain: number; release: number; gain: number } {
  if (type === "digital") {
    return { oscillator: "square", frequency: 800, attack: 0.005, sustain: 0.08, release: 0.03, gain: 0.4 };
  }
  if (type === "ambient") {
    return { oscillator: "sine", frequency: 440, attack: 0.05, sustain: 0.15, release: 0.1, gain: 0.3 };
  }
  if (type === "lowfreq") {
    return { oscillator: "sine", frequency: 100, attack: 0.02, sustain: 0.12, release: 0.08, gain: 0.6 };
  }
  if (type === "custom") {
    return { oscillator: "sine", frequency: settings.audioFrequency, attack: 0.01, sustain: 0.1, release: 0.05, gain: 0.5 };
  }
  return { oscillator: "sine", frequency: 667, attack: 0.01, sustain: 0.1, release: 0.05, gain: 0.5 };
}

function playHeartbeatAudio(options: { force?: boolean } = {}): void {
  if (!settings.audioEnabled || settings.audioType === "none" || (!options.force && connection !== "live")) {
    return;
  }
  if (!ensureAudioContext() || !audioContext || !audioGain) {
    return;
  }
  void audioContext.resume();
  const profile = audioProfile(settings.audioType);
  const oscillator = audioContext.createOscillator();
  const envelope = audioContext.createGain();
  const now = audioContext.currentTime;
  oscillator.type = profile.oscillator;
  oscillator.frequency.value = profile.frequency;
  envelope.gain.setValueAtTime(0, now);
  envelope.gain.linearRampToValueAtTime(profile.gain, now + profile.attack);
  envelope.gain.setValueAtTime(profile.gain, now + profile.attack + profile.sustain);
  envelope.gain.linearRampToValueAtTime(0, now + profile.attack + profile.sustain + profile.release);
  oscillator.connect(envelope);
  envelope.connect(audioGain);
  oscillator.start(now);
  oscillator.stop(now + profile.attack + profile.sustain + profile.release + 0.01);
}

function triggerBeatAnimation(): void {
  const bpmDisplay = byId("bpm-display");
  if (!bpmDisplay) return;
  bpmDisplay.classList.remove("live-beat");
  void bpmDisplay.offsetWidth;
  bpmDisplay.classList.add("live-beat");
  if (beatAnimationTimer !== null) {
    window.clearTimeout(beatAnimationTimer);
  }
  beatAnimationTimer = window.setTimeout(() => {
    bpmDisplay.classList.remove("live-beat");
    beatAnimationTimer = null;
  }, 460);
}

function triggerHeartbeatEffects(nowMs: number, playAudio = true): void {
  lastBeatAtMs = nowMs;
  triggerBeatAnimation();
  if (playAudio) {
    playHeartbeatAudio();
  }
  const interval = beatIntervalMsFromHr(status?.hr_bpm ?? null);
  nextEstimatedBeatAtMs = interval == null ? Number.POSITIVE_INFINITY : nowMs + interval;
}

function armEstimatedBeat(nowMs: number, hrBpm: number | null | undefined): void {
  const interval = beatIntervalMsFromHr(hrBpm);
  if (interval == null) {
    nextEstimatedBeatAtMs = Number.POSITIVE_INFINITY;
    return;
  }
  if (!Number.isFinite(nextEstimatedBeatAtMs) || nextEstimatedBeatAtMs < nowMs - interval) {
    nextEstimatedBeatAtMs = nowMs;
  }
}

function maybeTriggerEstimatedBeat(nowMs: number, connected: boolean): void {
  if (!connected) {
    nextEstimatedBeatAtMs = Number.POSITIVE_INFINITY;
    return;
  }
  const interval = beatIntervalMsFromHr(status?.hr_bpm ?? null);
  if (interval == null) {
    nextEstimatedBeatAtMs = Number.POSITIVE_INFINITY;
    return;
  }
  if (!Number.isFinite(nextEstimatedBeatAtMs)) {
    nextEstimatedBeatAtMs = nowMs + interval;
    return;
  }
  if (nowMs >= nextEstimatedBeatAtMs) {
    triggerHeartbeatEffects(nowMs);
  }
}

function setupAudioControls(): void {
  byId<HTMLInputElement>("audioToggle")?.addEventListener("change", (event) => setAudioEnabled(inputChecked(event)));
  byId<HTMLInputElement>("audioToggleSetting")?.addEventListener("change", (event) => setAudioEnabled(inputChecked(event)));
  byId<HTMLSelectElement>("audioTypeSelect")?.addEventListener("change", (event) => {
    settings.audioType = cleanAudioType(inputValue(event), defaultSettings.audioType);
    saveSettings();
  });
  byId<HTMLInputElement>("audioFrequency")?.addEventListener("input", (event) => {
    settings.audioFrequency = Math.round(Number(inputValue(event)));
    const value = byId("audioFrequencyValue");
    if (value) value.textContent = `${settings.audioFrequency} Hz`;
    saveSettings();
  });
  byId<HTMLInputElement>("audioVolume")?.addEventListener("input", (event) => {
    settings.audioVolume = Number(inputValue(event));
    const value = byId("audioVolumeValue");
    if (value) value.textContent = `${settings.audioVolume}%`;
    if (audioGain) audioGain.gain.value = settings.audioVolume / 100;
    saveSettings();
  });
}

function applyGridVisibility(): void {
  const grid = document.querySelector<HTMLElement>(".ecg-grid");
  if (!grid) return;
  grid.classList.toggle("grid-off", settings.ecgGridOpacity <= 0);
  grid.style.opacity = String(settings.ecgGridOpacity);
  grid.style.borderColor = settings.ecgGridColor;
}

function syncControlsFromSettings(): void {
  const bgColor = byId<HTMLInputElement>("bgColorPicker");
  if (bgColor) bgColor.value = settings.background.color;
  const themeColor = byId<HTMLInputElement>("themeColor");
  if (themeColor) themeColor.value = settings.accentColor;
  const particlesToggle = byId<HTMLInputElement>("particlesToggle");
  if (particlesToggle) particlesToggle.checked = settings.particlesEnabled;
  const particlesEffect = byId<HTMLSelectElement>("particlesEffect");
  if (particlesEffect) particlesEffect.value = settings.particleEffect;
  const particlesColor = byId<HTMLInputElement>("particlesColor");
  if (particlesColor) particlesColor.value = settings.particleColor;
  const particlesCount = byId<HTMLInputElement>("particlesCount");
  if (particlesCount) particlesCount.value = String(settings.particleCount);
  const particlesCountInput = byId<HTMLInputElement>("particlesCountInput");
  if (particlesCountInput) particlesCountInput.value = String(settings.particleCount);
  const particlesSize = byId<HTMLInputElement>("particlesSize");
  if (particlesSize) particlesSize.value = String(settings.particleSize);
  const particlesSizeInput = byId<HTMLInputElement>("particlesSizeInput");
  if (particlesSizeInput) particlesSizeInput.value = String(settings.particleSize);
  const effectHeartRateToggle = byId<HTMLInputElement>("effectHeartRateToggle");
  if (effectHeartRateToggle) effectHeartRateToggle.checked = settings.bindEffectsToHeartRate;
  const headerAudioToggle = byId<HTMLInputElement>("audioToggle");
  if (headerAudioToggle) headerAudioToggle.checked = settings.audioEnabled;
  const settingsAudioToggle = byId<HTMLInputElement>("audioToggleSetting");
  if (settingsAudioToggle) settingsAudioToggle.checked = settings.audioEnabled;
  const audioType = byId<HTMLSelectElement>("audioTypeSelect");
  if (audioType) audioType.value = settings.audioType;
  const audioFrequency = byId<HTMLInputElement>("audioFrequency");
  if (audioFrequency) audioFrequency.value = String(settings.audioFrequency);
  const audioFrequencyValue = byId("audioFrequencyValue");
  if (audioFrequencyValue) audioFrequencyValue.textContent = `${settings.audioFrequency} Hz`;
  const audioVolume = byId<HTMLInputElement>("audioVolume");
  if (audioVolume) audioVolume.value = String(settings.audioVolume);
  const audioVolumeValue = byId("audioVolumeValue");
  if (audioVolumeValue) audioVolumeValue.textContent = `${settings.audioVolume}%`;
  if (audioGain) audioGain.gain.value = settings.audioVolume / 100;
  const gridOpacity = byId<HTMLInputElement>("ecgGridOpacity");
  if (gridOpacity) gridOpacity.value = String(settings.ecgGridOpacity);
  const gridOpacityValue = byId("ecgGridOpacityValue");
  if (gridOpacityValue) gridOpacityValue.textContent = settings.ecgGridOpacity.toFixed(2);
  const gridColor = byId<HTMLInputElement>("ecgGridColor");
  if (gridColor) gridColor.value = settings.ecgGridColor;
  const lineColor = byId<HTMLInputElement>("ecgLineColor");
  if (lineColor) lineColor.value = settings.ecgLineColor;
  const lineWidth = byId<HTMLInputElement>("ecgLineWidth");
  if (lineWidth) lineWidth.value = String(settings.ecgLineWidth);
  const lineHeight = byId<HTMLInputElement>("ecgLineHeight");
  if (lineHeight) lineHeight.value = String(settings.ecgLineHeight);
  applyAccent();
  applyBackground();
  applyGridVisibility();
  three?.setParticlesEnabled(settings.particlesEnabled);
  three?.setParticleEffect(settings.particleEffect);
  three?.setParticleColor(settings.particleColor);
  three?.setParticleCount(settings.particleCount);
  three?.setParticleSize(settings.particleSize);
}

function resizeCanvas(canvas: HTMLCanvasElement): CanvasRenderingContext2D | null {
  const context = canvas.getContext("2d");
  if (!context) return null;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = Math.max(1, rect.width);
  const height = Math.max(1, rect.height);
  const pixelWidth = Math.round(width * dpr);
  const pixelHeight = Math.round(height * dpr);
  if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
    canvas.width = pixelWidth;
    canvas.height = pixelHeight;
  }
  context.setTransform(dpr, 0, 0, dpr, 0, 0);
  return context;
}

function drawEcg(buffer = displayedWaveform): void {
  const canvas = byId<HTMLCanvasElement>("ecg-canvas");
  if (!canvas) return;
  const context = resizeCanvas(canvas);
  if (!context) return;
  const width = canvas.width / (window.devicePixelRatio || 1);
  const height = canvas.height / (window.devicePixelRatio || 1);
  context.clearRect(0, 0, width, height);

  if (buffer.samples.length === 0) {
    return;
  }

  const min = Math.min(...buffer.samples);
  const max = Math.max(...buffer.samples);
  const span = Math.max(1e-6, max - min);
  const center = height * settings.ecgLineHeight;
  const amplitude = height * 0.38;
  const yFor = (value: number) => center - ((value - min) / span - 0.5) * amplitude * 2;
  const xFor = (index: number) => (buffer.samples.length <= 1 ? width : (index / (buffer.samples.length - 1)) * width);

  context.save();
  context.lineWidth = settings.ecgLineWidth;
  context.lineJoin = "round";
  context.lineCap = "round";
  context.strokeStyle = settings.ecgLineColor;
  context.shadowColor = settings.ecgLineColor;
  context.shadowBlur = 12;
  context.beginPath();
  buffer.samples.forEach((sample, index) => {
    const x = xFor(index);
    const y = yFor(sample);
    if (index === 0) {
      context.moveTo(x, y);
    } else {
      context.lineTo(x, y);
    }
  });
  context.stroke();
  context.restore();
}

function buildSnapshot(snapshot: Snapshot): void {
  waveform = { samples: [], rPeaks: [] };
  for (const batch of snapshot.batches) {
    waveform = appendEcgBatch(waveform, batch.samples, batch.r_peaks, MAX_SAMPLES);
  }
  displayedWaveform = { samples: [...waveform.samples], rPeaks: [...waveform.rPeaks] };
  nextEstimatedBeatAtMs = Number.POSITIVE_INFINITY;
  status = snapshot.status;
  const lastBatch = snapshot.batches.at(-1);
  if (lastBatch) {
    dedupe.lastSeq = lastBatch.seq;
    dedupe.lastTimestampMs = lastBatch.timestamp_ms;
    lastSeq = lastBatch.seq;
  }
}

function applyLiveBatch(batch: LiveEcgBatch): void {
  if (!shouldAcceptLiveBatch(batch, dedupe)) return;
  const nowMs = performance.now();
  lastSeq = batch.seq;
  waveform = appendEcgBatch(waveform, batch.samples, batch.r_peaks, MAX_SAMPLES);
  if (batch.hr_bpm != null || batch.sqi != null || batch.lead_off) {
    status = {
      type: "status",
      session_id: batch.session_id,
      state: batch.lead_off ? "signal_lost" : "live",
      timestamp_ms: batch.timestamp_ms,
      last_ingest_at_ms: batch.timestamp_ms,
      hr_bpm: batch.hr_bpm ?? status?.hr_bpm ?? null,
      sqi: batch.sqi ?? status?.sqi ?? null,
      lead_off: batch.lead_off,
      packet_loss: status?.packet_loss ?? 0,
      crc_errors: status?.crc_errors ?? 0,
      note: status?.note ?? null
    };
  }
  if (batch.r_peaks.length > 0) {
    triggerHeartbeatEffects(nowMs);
  } else {
    armEstimatedBeat(nowMs, batch.hr_bpm ?? status?.hr_bpm ?? null);
  }
}

function wsUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const url = new URL("/api/ws", `${protocol}//${window.location.host}`);
  url.searchParams.set("session_id", sessionId);
  return url.toString();
}

type LiveSessionResponse = {
  type: "live_session";
  session_id: string;
  viewer_token: string;
  viewer_url: string;
};

function isLiveSessionResponse(value: unknown): value is LiveSessionResponse {
  return (
    isRecord(value) &&
    value.type === "live_session" &&
    typeof value.session_id === "string" &&
    typeof value.viewer_token === "string" &&
    typeof value.viewer_url === "string"
  );
}

async function loadPublicLiveSession(): Promise<boolean> {
  try {
    const response = await fetch("/api/live-session", { cache: "no-store" });
    const body: unknown = await response.json();
    if (!response.ok || !isLiveSessionResponse(body)) {
      updateError(isRecord(body) && typeof body.error === "string" ? body.error : "live session unavailable");
      connection = "missing_config";
      updateStatusUi();
      return false;
    }
    sessionId = body.session_id;
    viewerToken = body.viewer_token;
    connection = "connecting";
    updateStatusUi();
    return true;
  } catch (error) {
    updateError(error instanceof Error ? error.message : "failed to load live session");
    connection = "missing_config";
    updateStatusUi();
    return false;
  }
}

function reconnectNow(): void {
  if (reconnectTimer !== null) {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  closed = false;
  attempt = 0;
  socket?.close();
  connect();
}

function connect(): void {
  if (!sessionId || !viewerToken || closed) {
    return;
  }
  connection = attempt === 0 ? "connecting" : "reconnecting";
  updateStatusUi();
  socket = new WebSocket(wsUrl());
  socket.addEventListener("open", () => {
    socket?.send(JSON.stringify({ type: "auth", role: "viewer", viewer_token: viewerToken }));
  });
  socket.addEventListener("message", (event) => {
    let raw: unknown;
    try {
      raw = JSON.parse(String(event.data));
    } catch {
      updateError("收到无法解析的服务器消息");
      return;
    }
    const parsed = ServerEventSchema.safeParse(raw);
    if (!parsed.success) {
      updateError("收到不支持的服务器消息");
      return;
    }
    const message = parsed.data;
    if (message.type === "snapshot") {
      buildSnapshot(message);
      connection = "live";
      attempt = 0;
    } else if (message.type === "ecg_batch") {
      applyLiveBatch(message);
    } else if (message.type === "status") {
      status = message;
    } else if (message.type === "signal_lost") {
      status = status
        ? { ...status, state: "signal_lost", lead_off: true, timestamp_ms: message.timestamp_ms }
        : {
            type: "status",
            session_id: message.session_id,
            state: "signal_lost",
            timestamp_ms: message.timestamp_ms,
            last_ingest_at_ms: null,
            hr_bpm: null,
            sqi: null,
            lead_off: true,
            packet_loss: 0,
            crc_errors: 0,
            note: null
          };
    } else if (message.type === "session_stopped") {
      connection = "closed";
      closed = true;
      socket?.close();
    } else if (message.type === "error") {
      updateError(message.message);
    }
    updateStatusUi();
  });
  socket.addEventListener("close", () => {
    if (closed) return;
    connection = "reconnecting";
    updateStatusUi();
    reconnectTimer = window.setTimeout(connect, reconnectDelayMs(attempt++));
  });
  socket.addEventListener("error", () => {
    updateError("WebSocket connection error");
  });
}

async function connectInitial(): Promise<void> {
  if (publicLiveMode && (!sessionId || !viewerToken)) {
    if (!(await loadPublicLiveSession())) {
      return;
    }
  }
  connect();
}

function animate(time: number): void {
  const connected = connection === "live" && status?.state === "live";
  const nowMs = performance.now();
  maybeTriggerEstimatedBeat(nowMs, connected);
  const pulse = settings.bindEffectsToHeartRate ? pulseFromBeatAge(nowMs - lastBeatAtMs, connected) : 0;
  three?.updateThree(time, pulse, settings.effectIntensity, settings.effectSpeed);
  three?.renderThree();
  displayedWaveform = smoothEcgBuffer(displayedWaveform, waveform, 0.22);
  drawEcg(displayedWaveform);
  window.requestAnimationFrame(animate);
}

async function initThreeRuntime(): Promise<void> {
  try {
    const moduleUrl = "/xinsu/modules/three.js";
    const nativeImport = new Function("url", "return import(url)") as (url: string) => Promise<unknown>;
    const module = (await nativeImport(moduleUrl)) as ThreeRuntime;
    three = module;
    three.initThree("canvas-container", "style1");
    three.setEffectHeartRateConfig({
      enabled: true,
      intensity: settings.effectIntensity,
      mode: "pulse",
      glowEffect: true,
      glowColor: settings.accentColor,
      glowIntensity: 2
    });
    syncControlsFromSettings();
    window.addEventListener("resize", () => {
      three?.onWindowResize();
    });
  } catch (error) {
    updateError(error instanceof Error ? `Three 初始化失败: ${error.message}` : "Three 初始化失败");
  }
}

function init(): void {
  applyBranding();
  setupSettingsPanel();
  setupBackgroundControls();
  setupThemeControls();
  setupParticleControls();
  setupHeartRateControls();
  setupEcgControls();
  setupAudioControls();
  syncControlsFromSettings();
  updateStatusUi();
  void initThreeRuntime();
  void connectInitial();
  window.requestAnimationFrame(animate);
}

init();
