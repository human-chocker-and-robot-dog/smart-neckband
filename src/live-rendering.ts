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

export function solidBackgroundChoice(color = "#000000"): BackgroundChoice {
  return {
    type: "color",
    color,
    image: ""
  };
}

export function pulseFromBeatAge(ageMs: number, connected: boolean): number {
  if (!connected || ageMs < 0 || ageMs > 420) {
    return 0;
  }
  const normalized = 1 - ageMs / 420;
  return Math.max(0, normalized * normalized);
}
