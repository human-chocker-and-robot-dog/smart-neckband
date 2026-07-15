export type EcgPointBuffer = {
  samples: number[];
  rPeaks: number[];
};

export type BackgroundChoice = {
  type: "color" | "image";
  color: string;
  image: string;
};

export function trimEcgBuffer(buffer: EcgPointBuffer, maxSamples: number): EcgPointBuffer {
  if (buffer.samples.length <= maxSamples) {
    return buffer;
  }
  const dropped = buffer.samples.length - maxSamples;
  return {
    samples: buffer.samples.slice(dropped),
    rPeaks: buffer.rPeaks.filter((peak) => peak >= dropped).map((peak) => peak - dropped)
  };
}

export function appendEcgBatch(
  buffer: EcgPointBuffer,
  samples: number[],
  rPeaks: number[],
  maxSamples: number
): EcgPointBuffer {
  const offset = buffer.samples.length;
  const nextPeaks = rPeaks.filter((peak) => peak >= 0 && peak < samples.length).map((peak) => offset + peak);
  return trimEcgBuffer(
    {
      samples: [...buffer.samples, ...samples],
      rPeaks: [...buffer.rPeaks, ...nextPeaks]
    },
    maxSamples
  );
}

export function smoothEcgBuffer(current: EcgPointBuffer, target: EcgPointBuffer, alpha: number): EcgPointBuffer {
  const boundedAlpha = Math.max(0, Math.min(1, alpha));
  if (target.samples.length === 0) {
    return { samples: [], rPeaks: [] };
  }
  if (current.samples.length === 0 || boundedAlpha >= 1) {
    return { samples: [...target.samples], rPeaks: [...target.rPeaks] };
  }

  const offset = target.samples.length - current.samples.length;
  const samples = target.samples.map((targetValue, index) => {
    const currentIndex = index - offset;
    const currentValue =
      currentIndex >= 0 && currentIndex < current.samples.length ? current.samples[currentIndex] : targetValue;
    return currentValue + (targetValue - currentValue) * boundedAlpha;
  });
  return { samples, rPeaks: [...target.rPeaks] };
}

export function solidBackgroundChoice(color = "#000000"): BackgroundChoice {
  return {
    type: "color",
    color,
    image: ""
  };
}

export function beatIntervalMsFromHr(hrBpm: number | null | undefined): number | null {
  if (hrBpm == null || !Number.isFinite(hrBpm) || hrBpm < 20 || hrBpm > 240) {
    return null;
  }
  return 60_000 / hrBpm;
}

export function scrollingSampleOffset(elapsedMs: number, sampleRateHz: number, maxOffsetSamples: number): number {
  if (!Number.isFinite(elapsedMs) || elapsedMs <= 0) {
    return 0;
  }
  return Math.min(maxOffsetSamples, (elapsedMs * sampleRateHz) / 1000);
}

export function visibleEcgWindowSamples(sampleCount: number, scrollOffsetSamples: number, maxSamples: number): number {
  return Math.max(1, Math.min(maxSamples, sampleCount + Math.max(0, scrollOffsetSamples)));
}

export function desaturateHexColor(color: string, saturationRatio: number): string {
  const match = /^#([0-9a-f]{6})$/i.exec(color);
  if (!match) {
    return color;
  }

  const value = Number.parseInt(match[1], 16);
  const red = (value >> 16) & 0xff;
  const green = (value >> 8) & 0xff;
  const blue = value & 0xff;
  const gray = red * 0.2126 + green * 0.7152 + blue * 0.0722;
  const ratio = Math.max(0, Math.min(1, saturationRatio));
  const mix = (channel: number) => Math.round(gray + (channel - gray) * ratio);
  return `rgb(${mix(red)}, ${mix(green)}, ${mix(blue)})`;
}

export function pulseFromBeatAge(ageMs: number, connected: boolean): number {
  if (!connected || ageMs < 0 || ageMs > 620) {
    return 0;
  }
  const normalized = 1 - ageMs / 620;
  return Math.max(0, normalized * normalized);
}
