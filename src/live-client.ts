import type { LiveEcgBatch } from "../lib/protocol";

export type DedupeState = {
  lastSeq: number;
  lastTimestampMs: number;
};

export function shouldAcceptLiveBatch(batch: Pick<LiveEcgBatch, "seq" | "timestamp_ms">, state: DedupeState): boolean {
  if (batch.seq <= state.lastSeq) {
    return false;
  }
  if (batch.timestamp_ms < state.lastTimestampMs) {
    return false;
  }
  state.lastSeq = batch.seq;
  state.lastTimestampMs = batch.timestamp_ms;
  return true;
}

export function reconnectDelayMs(attempt: number, random = Math.random): number {
  const base = Math.min(30_000, 500 * 2 ** Math.max(0, attempt));
  const jitter = base * 0.35 * random();
  return Math.round(base + jitter);
}

export function trimToRecentSamples(samples: number[], maxSamples: number): number[] {
  if (samples.length <= maxSamples) {
    return samples;
  }
  return samples.slice(samples.length - maxSamples);
}
