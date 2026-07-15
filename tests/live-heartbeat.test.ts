import { AddressInfo } from "node:net";
import { describe, expect, it } from "vitest";
import WebSocket from "ws";
import { hashToken } from "../lib/auth.js";
import { publicBaseUrlFromHeaders } from "../lib/http.js";
import { FakeRedis } from "../lib/redis.js";
import { ECG_SAMPLE_RATE_HZ, EcgBatch, ServerEvent } from "../lib/protocol.js";
import { deriveState, SessionManager } from "../lib/session-manager.js";
import { createHeartbeatServer } from "../lib/ws-server.js";
import { reconnectDelayMs, shouldAcceptLiveBatch, shouldLoadPublicLiveSession } from "../src/live-client.js";
import {
  appendEcgBatch,
  beatIntervalMsFromHr,
  pulseFromBeatAge,
  smoothEcgBuffer,
  solidBackgroundChoice,
  trimEcgBuffer
} from "../src/live-rendering.js";

function testBatch(seq: number, overrides: Partial<EcgBatch> = {}): EcgBatch {
  return {
    type: "ecg_batch",
    seq,
    timestamp_ms: 1_000 + seq * 40,
    sample_rate: ECG_SAMPLE_RATE_HZ,
    samples: [0.1, 0.2, 0.4, 0.2],
    r_peaks: [2],
    hr_bpm: 72,
    sqi: 0.91,
    lead_off: false,
    ...overrides
  };
}

async function waitForMessage<T>(
  socket: WebSocket,
  predicate: (message: T) => boolean,
  timeoutMs = 2_000
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => {
      socket.off("message", onMessage);
      reject(new Error("timed out waiting for websocket message"));
    }, timeoutMs);
    const onMessage = (data: WebSocket.RawData) => {
      const message = JSON.parse(data.toString()) as T;
      if (predicate(message)) {
        clearTimeout(timer);
        socket.off("message", onMessage);
        resolve(message);
      }
    };
    socket.on("message", onMessage);
  });
}

async function openSocket(url: string): Promise<WebSocket> {
  const socket = new WebSocket(url);
  await new Promise<void>((resolve, reject) => {
    socket.once("open", resolve);
    socket.once("error", reject);
  });
  return socket;
}

describe("session manager", () => {
  it("creates sessions with hashed tokens and TTL", async () => {
    const redis = new FakeRedis();
    const manager = new SessionManager({ redis, baseUrl: "https://example.test", now: () => 10_000 });

    const created = await manager.createSession("127.0.0.1");
    const metaRaw = await redis.get(manager.metaKey(created.session_id));
    const meta = JSON.parse(metaRaw ?? "{}") as { ingest_token_hash: string; viewer_token_hash: string };

    expect(created.ingest_token).not.toHaveLength(0);
    expect(created.viewer_token).not.toHaveLength(0);
    expect(meta.ingest_token_hash).toBe(hashToken(created.ingest_token));
    expect(meta.viewer_token_hash).toBe(hashToken(created.viewer_token));
    expect(metaRaw).not.toContain(created.ingest_token);
    expect(metaRaw).not.toContain(created.viewer_token);
    expect(redis.expireCalls.get(manager.metaKey(created.session_id))).toBe(43_200);
    expect(created.viewer_url).toContain(created.session_id);
  });

  it("rejects wrong ingest and viewer tokens", async () => {
    const redis = new FakeRedis();
    const manager = new SessionManager({ redis });
    const created = await manager.createSession("127.0.0.1");

    await expect(manager.validateIngest(created.session_id, "wrong-token-value-that-is-long-enough")).resolves.toBe(false);
    await expect(manager.validateViewer(created.session_id, "wrong-token-value-that-is-long-enough")).resolves.toBe(false);
  });

  it("keeps only one active producer lock", async () => {
    const redis = new FakeRedis();
    const manager = new SessionManager({ redis });
    const created = await manager.createSession("127.0.0.1");

    await expect(manager.acquireIngestLock(created.session_id, "one")).resolves.toBe(true);
    await expect(manager.acquireIngestLock(created.session_id, "two")).resolves.toBe(false);
    await manager.releaseIngestLock(created.session_id, "one");
    await expect(manager.acquireIngestLock(created.session_id, "two")).resolves.toBe(true);
  });

  it("updates snapshot and rejects duplicate sequence numbers", async () => {
    const redis = new FakeRedis();
    const manager = new SessionManager({ redis });
    const created = await manager.createSession("127.0.0.1");

    await expect(manager.validateSequence(created.session_id, 1)).resolves.toBe(true);
    await manager.updateFromEcgBatch(created.session_id, testBatch(1));
    await expect(manager.validateSequence(created.session_id, 1)).resolves.toBe(false);
    const snapshot = await manager.getSnapshot(created.session_id);

    expect(snapshot.snapshot).toBe(true);
    expect(snapshot.batches).toHaveLength(1);
    expect(snapshot.batches[0].snapshot).toBe(true);
  });

  it("derives stale, offline, and signal_lost status", () => {
    expect(deriveState({ lead_off: false, last_ingest_at_ms: 7_100 }, 10_000)).toBe("live");
    expect(deriveState({ lead_off: false, last_ingest_at_ms: 6_900 }, 10_000)).toBe("stale");
    expect(deriveState({ lead_off: false, last_ingest_at_ms: 0 }, 10_000)).toBe("offline");
    expect(deriveState({ lead_off: true, last_ingest_at_ms: 9_999 }, 10_000)).toBe("signal_lost");
  });

  it("stops sessions and deletes volatile Redis keys", async () => {
    const redis = new FakeRedis();
    const manager = new SessionManager({ redis });
    const created = await manager.createSession("127.0.0.1");
    await manager.updateFromEcgBatch(created.session_id, testBatch(1));

    await expect(manager.stopSession(created.session_id)).resolves.toBe(true);

    await expect(redis.get(manager.statusKey(created.session_id))).resolves.toBeNull();
    await expect(redis.get(manager.snapshotKey(created.session_id))).resolves.toBeNull();
    await expect(manager.validateViewer(created.session_id, created.viewer_token)).resolves.toBe(false);
  });

  it("limits session creation by IP", async () => {
    const redis = new FakeRedis();
    const manager = new SessionManager({ redis, createLimitPerMinute: 1 });

    await manager.createSession("127.0.0.1");
    await expect(manager.createSession("127.0.0.1")).rejects.toThrow("rate limit");
  });

  it("accepts configured fixed live session credentials without Redis meta", async () => {
    const redis = new FakeRedis();
    const liveSession = {
      sessionId: "sess_live_main_0123456789abcdef",
      ingestToken: "ingest-token-0123456789abcdef0123456789abcdef",
      viewerToken: "viewer-token-0123456789abcdef0123456789abcdef"
    };
    const manager = new SessionManager({ redis, liveSession });

    await expect(manager.validateIngest(liveSession.sessionId, liveSession.ingestToken)).resolves.toBe(true);
    await expect(manager.validateViewer(liveSession.sessionId, liveSession.viewerToken)).resolves.toBe(true);
    await expect(manager.acquireIngestLock(liveSession.sessionId, "producer")).resolves.toBe(true);
    await manager.updateFromEcgBatch(liveSession.sessionId, testBatch(1));

    const snapshot = await manager.getSnapshot(liveSession.sessionId);
    expect(snapshot.session_id).toBe(liveSession.sessionId);
    expect(snapshot.batches).toHaveLength(1);
  });
});

describe("HTTP deployment helpers", () => {
  it("derives public base URL from forwarded Vercel headers", () => {
    expect(
      publicBaseUrlFromHeaders(
        {
          "x-forwarded-proto": "https",
          "x-forwarded-host": "ai-smart-collar-v0-live-heartbeat.vercel.app"
        },
        undefined
      )
    ).toBe("https://ai-smart-collar-v0-live-heartbeat.vercel.app");
  });

  it("lets PUBLIC_BASE_URL override forwarded headers", () => {
    expect(
      publicBaseUrlFromHeaders(
        {
          "x-forwarded-proto": "https",
          "x-forwarded-host": "preview.vercel.app"
        },
        "https://live.example.test"
      )
    ).toBe("https://live.example.test");
  });
});

describe("websocket flow", () => {
  it("broadcasts ingest data to a viewer through Redis across server instances", async () => {
    const redis = new FakeRedis();
    const manager = new SessionManager({ redis });
    const created = await manager.createSession("127.0.0.1");
    const serverA = createHeartbeatServer({ redis });
    const serverB = createHeartbeatServer({ redis });
    await new Promise<void>((resolve) => serverA.listen(0, resolve));
    await new Promise<void>((resolve) => serverB.listen(0, resolve));
    const portA = (serverA.address() as AddressInfo).port;
    const portB = (serverB.address() as AddressInfo).port;

    const viewer = await openSocket(`ws://127.0.0.1:${portB}/api/ws?session_id=${created.session_id}`);
    viewer.send(JSON.stringify({ type: "auth", role: "viewer", viewer_token: created.viewer_token }));
    await waitForMessage<ServerEvent>(viewer, (message) => message.type === "snapshot");

    const ingest = await openSocket(`ws://127.0.0.1:${portA}/api/ws`);
    ingest.send(
      JSON.stringify({
        type: "auth",
        role: "ingest",
        session_id: created.session_id,
        token: created.ingest_token
      })
    );
    await waitForMessage<{ type: string }>(ingest, (message) => message.type === "auth_ok");
    ingest.send(JSON.stringify(testBatch(1)));
    const live = await waitForMessage<ServerEvent>(
      viewer,
      (message) => message.type === "ecg_batch" && message.seq === 1
    );

    expect(live.type).toBe("ecg_batch");
    viewer.close();
    ingest.close();
    await new Promise<void>((resolve) => serverA.close(() => resolve()));
    await new Promise<void>((resolve) => serverB.close(() => resolve()));
  });

  it("broadcasts signal_lost immediately on lead-off", async () => {
    const redis = new FakeRedis();
    const manager = new SessionManager({ redis });
    const created = await manager.createSession("127.0.0.1");
    const subscriber = redis.duplicate();
    const received: ServerEvent[] = [];
    subscriber.onMessage((_channel, message) => received.push(JSON.parse(message) as ServerEvent));
    await subscriber.subscribe(manager.channel(created.session_id));

    await manager.updateFromEcgBatch(created.session_id, testBatch(1, { lead_off: true }));

    expect(received.some((message) => message.type === "signal_lost")).toBe(true);
    await subscriber.quit();
  });
});

describe("viewer client helpers", () => {
  it("deduplicates live frames by sequence and timestamp", () => {
    const state = { lastSeq: 10, lastTimestampMs: 1000 };

    expect(shouldAcceptLiveBatch({ seq: 10, timestamp_ms: 1100 }, state)).toBe(false);
    expect(shouldAcceptLiveBatch({ seq: 11, timestamp_ms: 900 }, state)).toBe(false);
    expect(shouldAcceptLiveBatch({ seq: 11, timestamp_ms: 1100 }, state)).toBe(true);
  });

  it("uses exponential reconnect backoff with jitter", () => {
    expect(reconnectDelayMs(0, () => 0)).toBe(500);
    expect(reconnectDelayMs(3, () => 0)).toBe(4000);
    expect(reconnectDelayMs(20, () => 0)).toBe(30000);
    expect(reconnectDelayMs(1, () => 1)).toBe(1350);
  });

  it("loads fixed public live session only for bare /live", () => {
    expect(shouldLoadPublicLiveSession("/live", "", "")).toBe(true);
    expect(shouldLoadPublicLiveSession("/live/", "", "")).toBe(true);
    expect(shouldLoadPublicLiveSession("/viewer", "", "")).toBe(false);
    expect(shouldLoadPublicLiveSession("/live", "sess_abc", "")).toBe(false);
  });
});

describe("live reference-shell rendering helpers", () => {
  it("defaults to a pure solid background choice", () => {
    expect(solidBackgroundChoice()).toEqual({ type: "color", color: "#000000", image: "" });
  });

  it("keeps R peak indexes aligned when trimming ECG buffers", () => {
    const buffer = appendEcgBatch({ samples: [1, 2, 3], rPeaks: [1] }, [4, 5, 6], [1], 4);

    expect(buffer.samples).toEqual([3, 4, 5, 6]);
    expect(buffer.rPeaks).toEqual([2]);
    expect(trimEcgBuffer({ samples: [1, 2, 3], rPeaks: [0, 2] }, 2)).toEqual({ samples: [2, 3], rPeaks: [1] });
  });

  it("smooths visible ECG samples while preserving target R peaks", () => {
    const current = { samples: [0, 0, 0], rPeaks: [1] };
    const target = { samples: [0, 10, 20, 30], rPeaks: [3] };

    expect(smoothEcgBuffer(current, target, 0.5)).toEqual({ samples: [0, 5, 10, 15], rPeaks: [3] });
  });

  it("only produces visual pulse while connected and close to a real beat", () => {
    expect(pulseFromBeatAge(100, true)).toBeGreaterThan(0);
    expect(pulseFromBeatAge(100, false)).toBe(0);
    expect(pulseFromBeatAge(700, true)).toBe(0);
  });

  it("derives live beat intervals from valid HR only", () => {
    expect(beatIntervalMsFromHr(80)).toBe(750);
    expect(beatIntervalMsFromHr(null)).toBeNull();
    expect(beatIntervalMsFromHr(5)).toBeNull();
    expect(beatIntervalMsFromHr(300)).toBeNull();
  });
});
