import {
  DEFAULT_SESSION_TTL_SECONDS,
  EcgBatch,
  ECG_SAMPLE_RATE_HZ,
  HealthEventUpdate,
  LiveEcgBatch,
  LiveHealthEvent,
  LiveTelemetry,
  OFFLINE_AFTER_MS,
  PublicStatus,
  SessionIdSchema,
  SessionState,
  Snapshot,
  SNAPSHOT_SECONDS,
  STALE_AFTER_MS,
  StatusUpdate,
  TelemetryUpdate
} from "./protocol.js";
import { createSessionId, createToken, hashToken, safeEqualHash } from "./auth.js";
import { getLiveSessionConfig, LiveSessionConfig } from "./live-session.js";
import { RedisLike } from "./redis.js";

export type SessionMeta = {
  session_id: string;
  ingest_token_hash: string;
  viewer_token_hash: string;
  created_at_ms: number;
  expires_at_ms: number;
  ttl_seconds: number;
  stopped: boolean;
};

export type CreatedSession = {
  session_id: string;
  ingest_token: string;
  viewer_token: string;
  viewer_url: string;
  expires_at_ms: number;
};

export type SessionManagerOptions = {
  redis: RedisLike;
  now?: () => number;
  baseUrl?: string;
  ttlSeconds?: number;
  createLimitPerMinute?: number;
  wsUpgradeLimitPerMinute?: number;
  liveSession?: LiveSessionConfig | null;
};

export class SessionManager {
  readonly redis: RedisLike;
  private readonly now: () => number;
  private readonly baseUrl: string;
  private readonly ttlSeconds: number;
  private readonly createLimitPerMinute: number;
  private readonly wsUpgradeLimitPerMinute: number;
  private readonly liveSession: LiveSessionConfig | null;

  constructor(options: SessionManagerOptions) {
    this.redis = options.redis;
    this.now = options.now ?? Date.now;
    this.baseUrl = options.baseUrl ?? process.env.PUBLIC_BASE_URL ?? "http://localhost:5173";
    this.ttlSeconds = options.ttlSeconds ?? Number(process.env.SESSION_TTL_SECONDS ?? DEFAULT_SESSION_TTL_SECONDS);
    this.createLimitPerMinute = options.createLimitPerMinute ?? Number(process.env.SESSION_CREATE_LIMIT_PER_MINUTE ?? 10);
    this.wsUpgradeLimitPerMinute = options.wsUpgradeLimitPerMinute ?? Number(process.env.WS_UPGRADE_LIMIT_PER_MINUTE ?? 60);
    this.liveSession = options.liveSession === undefined ? getLiveSessionConfig() : options.liveSession;
  }

  metaKey(sessionId: string): string {
    return `session:${sessionId}:meta`;
  }

  statusKey(sessionId: string): string {
    return `session:${sessionId}:status`;
  }

  snapshotKey(sessionId: string): string {
    return `session:${sessionId}:snapshot`;
  }

  telemetryKey(sessionId: string): string {
    return `session:${sessionId}:telemetry`;
  }

  recentEventsKey(sessionId: string): string {
    return `session:${sessionId}:recent_events`;
  }

  ingestLockKey(sessionId: string): string {
    return `session:${sessionId}:ingest_lock`;
  }

  lastSeqKey(sessionId: string): string {
    return `session:${sessionId}:last_seq`;
  }

  channel(sessionId: string): string {
    return `session:${sessionId}:live`;
  }

  async enforceSessionCreateRate(ip: string): Promise<void> {
    await this.enforceRate(`rate:session_create:${ip}`, this.createLimitPerMinute);
  }

  async enforceWsUpgradeRate(ip: string): Promise<void> {
    await this.enforceRate(`rate:ws_upgrade:${ip}`, this.wsUpgradeLimitPerMinute);
  }

  async createSession(ip: string): Promise<CreatedSession> {
    await this.enforceSessionCreateRate(ip);
    const sessionId = createSessionId();
    const ingestToken = createToken();
    const viewerToken = createToken();
    const now = this.now();
    const meta: SessionMeta = {
      session_id: sessionId,
      ingest_token_hash: hashToken(ingestToken),
      viewer_token_hash: hashToken(viewerToken),
      created_at_ms: now,
      expires_at_ms: now + this.ttlSeconds * 1000,
      ttl_seconds: this.ttlSeconds,
      stopped: false
    };
    await this.redis.set(this.metaKey(sessionId), JSON.stringify(meta), "EX", this.ttlSeconds);
    const viewerUrl = new URL("/viewer", this.baseUrl);
    viewerUrl.searchParams.set("session_id", sessionId);
    viewerUrl.searchParams.set("viewer_token", viewerToken);
    return {
      session_id: sessionId,
      ingest_token: ingestToken,
      viewer_token: viewerToken,
      viewer_url: viewerUrl.toString(),
      expires_at_ms: meta.expires_at_ms
    };
  }

  async getMeta(sessionId: string): Promise<SessionMeta | null> {
    const parsedId = SessionIdSchema.safeParse(sessionId);
    if (!parsedId.success) {
      return null;
    }
    const liveMeta = this.getLiveMeta(sessionId);
    if (liveMeta) {
      return liveMeta;
    }
    const raw = await this.redis.get(this.metaKey(sessionId));
    if (!raw) {
      return null;
    }
    const meta = JSON.parse(raw) as SessionMeta;
    if (meta.stopped || meta.expires_at_ms <= this.now()) {
      return null;
    }
    return meta;
  }

  async validateIngest(sessionId: string, token: string): Promise<boolean> {
    const meta = await this.getMeta(sessionId);
    return meta !== null && safeEqualHash(token, meta.ingest_token_hash);
  }

  async validateViewer(sessionId: string, token: string): Promise<boolean> {
    const meta = await this.getMeta(sessionId);
    return meta !== null && safeEqualHash(token, meta.viewer_token_hash);
  }

  async acquireIngestLock(sessionId: string, owner: string): Promise<boolean> {
    const meta = await this.getMeta(sessionId);
    if (!meta) {
      return false;
    }
    const result = await this.redis.set(this.ingestLockKey(sessionId), owner, "EX", 15, "NX");
    return result === "OK";
  }

  async refreshIngestLock(sessionId: string, owner: string): Promise<void> {
    const key = this.ingestLockKey(sessionId);
    if ((await this.redis.get(key)) === owner) {
      await this.redis.set(key, owner, "EX", 15);
    }
  }

  async releaseIngestLock(sessionId: string, owner: string): Promise<void> {
    const key = this.ingestLockKey(sessionId);
    if ((await this.redis.get(key)) === owner) {
      await this.redis.del(key);
    }
  }

  async validateSequence(sessionId: string, seq: number): Promise<boolean> {
    const key = this.lastSeqKey(sessionId);
    const previousRaw = await this.redis.get(key);
    if (previousRaw !== null && seq <= Number(previousRaw)) {
      return false;
    }
    await this.redis.set(key, String(seq), "EX", this.ttlSeconds);
    return true;
  }

  async nextSequence(sessionId: string): Promise<number> {
    const previousRaw = await this.redis.get(this.lastSeqKey(sessionId));
    return previousRaw === null ? 0 : Number(previousRaw) + 1;
  }

  async updateFromEcgBatch(sessionId: string, batch: EcgBatch): Promise<{ live: LiveEcgBatch; status: PublicStatus; signalLost: boolean }> {
    const live: LiveEcgBatch = {
      ...batch,
      session_id: sessionId,
      snapshot: false
    };
    await this.updateSnapshot(sessionId, live);
    const status = await this.updateStatus(sessionId, {
      type: "status",
      seq: batch.seq,
      timestamp_ms: batch.timestamp_ms,
      hr_bpm: batch.hr_bpm ?? null,
      sqi: batch.sqi ?? null,
      lead_off: batch.lead_off
    });
    await this.redis.publish(this.channel(sessionId), JSON.stringify(live));
    if (batch.lead_off) {
      await this.redis.publish(
        this.channel(sessionId),
        JSON.stringify({ type: "signal_lost", session_id: sessionId, timestamp_ms: this.now(), lead_off: true })
      );
    }
    await this.redis.publish(this.channel(sessionId), JSON.stringify(status));
    return { live, status, signalLost: batch.lead_off };
  }

  async updateStatus(sessionId: string, update: StatusUpdate): Promise<PublicStatus> {
    const previous = await this.getPublicStatus(sessionId);
    const now = this.now();
    const lastIngest = update.timestamp_ms ?? now;
    const status: PublicStatus = {
      type: "status",
      session_id: sessionId,
      state: update.lead_off ? "signal_lost" : "live",
      timestamp_ms: now,
      last_ingest_at_ms: lastIngest,
      hr_bpm: update.hr_bpm ?? previous?.hr_bpm ?? null,
      sqi: update.sqi ?? previous?.sqi ?? null,
      lead_off: update.lead_off ?? previous?.lead_off ?? false,
      packet_loss: update.packet_loss ?? previous?.packet_loss ?? 0,
      crc_errors: update.crc_errors ?? previous?.crc_errors ?? 0,
      note: update.note ?? previous?.note ?? null
    };
    status.state = deriveState(status, now);
    await this.redis.set(this.statusKey(sessionId), JSON.stringify(status), "EX", this.ttlSeconds);
    return status;
  }

  async updateTelemetry(sessionId: string, update: TelemetryUpdate): Promise<LiveTelemetry> {
    const telemetry: LiveTelemetry = { ...update, session_id: sessionId };
    await this.redis.set(this.telemetryKey(sessionId), JSON.stringify(telemetry), "EX", this.ttlSeconds);
    await this.redis.publish(this.channel(sessionId), JSON.stringify(telemetry));
    return telemetry;
  }

  async updateHealthEvent(sessionId: string, update: HealthEventUpdate): Promise<LiveHealthEvent> {
    const event: LiveHealthEvent = { ...update, session_id: sessionId };
    const raw = await this.redis.get(this.recentEventsKey(sessionId));
    const previous = raw ? (JSON.parse(raw) as LiveHealthEvent[]) : [];
    const existing = previous.find((item) => item.event_id === event.event_id);
    if (existing && existing.event_revision >= event.event_revision) {
      return existing;
    }
    const recent = [event, ...previous.filter((item) => item.event_id !== event.event_id)]
      .sort((left, right) => right.timestamp_ms - left.timestamp_ms)
      .slice(0, 20);
    await this.redis.set(this.recentEventsKey(sessionId), JSON.stringify(recent), "EX", this.ttlSeconds);
    await this.redis.publish(this.channel(sessionId), JSON.stringify(event));
    return event;
  }

  async getTelemetry(sessionId: string): Promise<LiveTelemetry | null> {
    const raw = await this.redis.get(this.telemetryKey(sessionId));
    return raw ? (JSON.parse(raw) as LiveTelemetry) : null;
  }

  async getRecentEvents(sessionId: string): Promise<LiveHealthEvent[]> {
    const raw = await this.redis.get(this.recentEventsKey(sessionId));
    return raw ? (JSON.parse(raw) as LiveHealthEvent[]).slice(0, 20) : [];
  }

  async getPublicStatus(sessionId: string): Promise<PublicStatus | null> {
    const raw = await this.redis.get(this.statusKey(sessionId));
    if (!raw) {
      return null;
    }
    const status = JSON.parse(raw) as PublicStatus;
    return {
      ...status,
      state: deriveState(status, this.now()),
      timestamp_ms: this.now()
    };
  }

  async getSnapshot(sessionId: string): Promise<Snapshot> {
    const raw = await this.redis.get(this.snapshotKey(sessionId));
    const batches = raw ? (JSON.parse(raw) as LiveEcgBatch[]) : [];
    return {
      type: "snapshot",
      snapshot: true,
      session_id: sessionId,
      generated_at_ms: this.now(),
      sample_rate: ECG_SAMPLE_RATE_HZ,
      batches: batches.map((batch) => ({ ...batch, snapshot: true })),
      status: await this.getPublicStatus(sessionId),
      telemetry: await this.getTelemetry(sessionId),
      recent_events: await this.getRecentEvents(sessionId)
    };
  }

  async stopSession(sessionId: string): Promise<boolean> {
    const meta = await this.getMeta(sessionId);
    if (!meta) {
      return false;
    }
    meta.stopped = true;
    await this.redis.set(this.metaKey(sessionId), JSON.stringify(meta), "EX", 60);
    await this.redis.del(
      this.statusKey(sessionId),
      this.snapshotKey(sessionId),
      this.telemetryKey(sessionId),
      this.recentEventsKey(sessionId),
      this.ingestLockKey(sessionId),
      this.lastSeqKey(sessionId)
    );
    await this.redis.publish(this.channel(sessionId), JSON.stringify({ type: "session_stopped", session_id: sessionId, timestamp_ms: this.now() }));
    return true;
  }

  private getLiveMeta(sessionId: string): SessionMeta | null {
    if (!this.liveSession || sessionId !== this.liveSession.sessionId) {
      return null;
    }
    return {
      session_id: this.liveSession.sessionId,
      ingest_token_hash: hashToken(this.liveSession.ingestToken),
      viewer_token_hash: hashToken(this.liveSession.viewerToken),
      created_at_ms: 0,
      expires_at_ms: Number.MAX_SAFE_INTEGER,
      ttl_seconds: this.ttlSeconds,
      stopped: false
    };
  }

  private async updateSnapshot(sessionId: string, batch: LiveEcgBatch): Promise<void> {
    const raw = await this.redis.get(this.snapshotKey(sessionId));
    const previous = raw ? (JSON.parse(raw) as LiveEcgBatch[]) : [];
    const cutoffMs = batch.timestamp_ms - SNAPSHOT_SECONDS * 1000;
    const trimmed = [...previous, batch].filter((item) => {
      const durationMs = (item.samples.length / item.sample_rate) * 1000;
      return item.timestamp_ms + durationMs >= cutoffMs;
    });
    await this.redis.set(this.snapshotKey(sessionId), JSON.stringify(trimmed), "EX", this.ttlSeconds);
  }

  private async enforceRate(key: string, limit: number): Promise<void> {
    const count = await this.redis.incr(key);
    if (count === 1) {
      await this.redis.expire(key, 60);
    }
    if (count > limit) {
      throw new Error("rate limit exceeded");
    }
  }
}

export function deriveState(status: Pick<PublicStatus, "lead_off" | "last_ingest_at_ms">, nowMs: number): SessionState {
  if (status.lead_off) {
    return "signal_lost";
  }
  if (status.last_ingest_at_ms === null) {
    return "offline";
  }
  const ageMs = nowMs - status.last_ingest_at_ms;
  if (ageMs >= OFFLINE_AFTER_MS) {
    return "offline";
  }
  if (ageMs >= STALE_AFTER_MS) {
    return "stale";
  }
  return "live";
}
