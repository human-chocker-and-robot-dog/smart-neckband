import type { PublicStatus } from "../lib/protocol";

export const VIEWER_SETTINGS_STORAGE_KEY = "smart-collar-live-beta-settings:v1";

export type ParticleEffect = "stars" | "pulse" | "wave";

export type ViewerSettings = {
  backgroundMode: "solid";
  backgroundColor: string;
  accentColor: string;
  showEcgGrid: boolean;
  particlesEnabled: boolean;
  particleEffect: ParticleEffect;
  particleColor: string;
  particleCount: number;
  bindEffectsToHeartRate: boolean;
  heartRateEffectIntensity: number;
};

export type BackgroundStyle = {
  backgroundColor: string;
  backgroundImage: "none";
};

export type ParticleDrive = {
  active: boolean;
  mode: "live" | "idle";
  bpm: number | null;
  intensity: number;
  speed: number;
  brightness: number;
  pulsePerSecond: number;
};

export const DEFAULT_VIEWER_SETTINGS: ViewerSettings = {
  backgroundMode: "solid",
  backgroundColor: "#05070c",
  accentColor: "#23f0aa",
  showEcgGrid: false,
  particlesEnabled: true,
  particleEffect: "stars",
  particleColor: "#8ee7ff",
  particleCount: 360,
  bindEffectsToHeartRate: true,
  heartRateEffectIntensity: 1
};

type StorageLike = Pick<Storage, "getItem" | "setItem">;

const COLOR_RE = /^#[0-9a-fA-F]{6}$/;
const PARTICLE_EFFECTS: ParticleEffect[] = ["stars", "pulse", "wave"];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function cleanColor(value: unknown, fallback: string): string {
  return typeof value === "string" && COLOR_RE.test(value) ? value : fallback;
}

function cleanBoolean(value: unknown, fallback: boolean): boolean {
  return typeof value === "boolean" ? value : fallback;
}

function cleanNumber(value: unknown, fallback: number, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return fallback;
  }
  return Math.min(max, Math.max(min, value));
}

function cleanParticleEffect(value: unknown, fallback: ParticleEffect): ParticleEffect {
  return typeof value === "string" && PARTICLE_EFFECTS.includes(value as ParticleEffect)
    ? (value as ParticleEffect)
    : fallback;
}

export function coerceViewerSettings(value: unknown): ViewerSettings {
  if (!isRecord(value)) {
    return { ...DEFAULT_VIEWER_SETTINGS };
  }

  return {
    backgroundMode: "solid",
    backgroundColor: cleanColor(value.backgroundColor, DEFAULT_VIEWER_SETTINGS.backgroundColor),
    accentColor: cleanColor(value.accentColor, DEFAULT_VIEWER_SETTINGS.accentColor),
    showEcgGrid: cleanBoolean(value.showEcgGrid, DEFAULT_VIEWER_SETTINGS.showEcgGrid),
    particlesEnabled: cleanBoolean(value.particlesEnabled, DEFAULT_VIEWER_SETTINGS.particlesEnabled),
    particleEffect: cleanParticleEffect(value.particleEffect, DEFAULT_VIEWER_SETTINGS.particleEffect),
    particleColor: cleanColor(value.particleColor, DEFAULT_VIEWER_SETTINGS.particleColor),
    particleCount: Math.round(cleanNumber(value.particleCount, DEFAULT_VIEWER_SETTINGS.particleCount, 120, 900)),
    bindEffectsToHeartRate: cleanBoolean(
      value.bindEffectsToHeartRate,
      DEFAULT_VIEWER_SETTINGS.bindEffectsToHeartRate
    ),
    heartRateEffectIntensity: cleanNumber(
      value.heartRateEffectIntensity,
      DEFAULT_VIEWER_SETTINGS.heartRateEffectIntensity,
      0.1,
      2
    )
  };
}

export function loadViewerSettings(storage: StorageLike | null): ViewerSettings {
  if (!storage) {
    return { ...DEFAULT_VIEWER_SETTINGS };
  }
  try {
    return coerceViewerSettings(JSON.parse(storage.getItem(VIEWER_SETTINGS_STORAGE_KEY) ?? "null"));
  } catch {
    return { ...DEFAULT_VIEWER_SETTINGS };
  }
}

export function saveViewerSettings(settings: ViewerSettings, storage: StorageLike | null): void {
  if (!storage) {
    return;
  }
  try {
    storage.setItem(VIEWER_SETTINGS_STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // Non-sensitive visual preferences are best-effort only.
  }
}

export function solidBackgroundStyle(settings: ViewerSettings): BackgroundStyle {
  return {
    backgroundColor: settings.backgroundColor,
    backgroundImage: "none"
  };
}

export function particleDriveFor(
  status: Pick<PublicStatus, "state" | "hr_bpm"> | null,
  settings: Pick<ViewerSettings, "particlesEnabled" | "bindEffectsToHeartRate" | "heartRateEffectIntensity">
): ParticleDrive {
  if (!settings.particlesEnabled) {
    return {
      active: false,
      mode: "idle",
      bpm: null,
      intensity: 0,
      speed: 0,
      brightness: 0,
      pulsePerSecond: 0
    };
  }

  const canBindToLiveHeart =
    settings.bindEffectsToHeartRate &&
    status?.state === "live" &&
    typeof status.hr_bpm === "number" &&
    Number.isFinite(status.hr_bpm);

  if (!canBindToLiveHeart) {
    return {
      active: true,
      mode: "idle",
      bpm: null,
      intensity: 0.35,
      speed: 0.22,
      brightness: 0.42,
      pulsePerSecond: 0
    };
  }

  const bpm = status.hr_bpm as number;
  const bpmScale = Math.min(1.9, Math.max(0.6, bpm / 78));
  const intensity = Math.min(3, Math.max(0.1, bpmScale * settings.heartRateEffectIntensity));

  return {
    active: true,
    mode: "live",
    bpm,
    intensity,
    speed: Math.min(1.45, 0.36 + bpmScale * 0.52),
    brightness: Math.min(1, 0.48 + intensity * 0.22),
    pulsePerSecond: bpm / 60
  };
}
