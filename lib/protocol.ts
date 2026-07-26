import { z } from "zod";

export const ECG_SAMPLE_RATE_HZ = 500;
export const SNAPSHOT_SECONDS = 10;
export const MAX_MESSAGE_BYTES = 128 * 1024;
export const DEFAULT_SESSION_TTL_SECONDS = 12 * 60 * 60;
export const STALE_AFTER_MS = 3_000;
export const OFFLINE_AFTER_MS = 10_000;

export const SessionIdSchema = z.string().regex(/^sess_[A-Za-z0-9_-]{16,64}$/);
export const TokenSchema = z.string().min(32).max(256);

export const IngestAuthSchema = z.object({
  type: z.literal("auth"),
  role: z.literal("ingest"),
  session_id: SessionIdSchema,
  token: TokenSchema
});

export const ViewerAuthSchema = z.object({
  type: z.literal("auth"),
  role: z.literal("viewer"),
  session_id: SessionIdSchema.optional(),
  viewer_token: TokenSchema
});

export const AuthMessageSchema = z.discriminatedUnion("role", [IngestAuthSchema, ViewerAuthSchema]);

export const AuthOkSchema = z.object({
  type: z.literal("auth_ok"),
  role: z.enum(["ingest", "viewer"]),
  session_id: SessionIdSchema,
  next_seq: z.number().int().nonnegative().optional()
});

export const IngestAckSchema = z.object({
  type: z.literal("ingest_ack"),
  message_type: z.enum(["ecg_batch", "status", "telemetry", "health_event"]),
  seq: z.number().int().nonnegative().nullable(),
  next_seq: z.number().int().nonnegative()
});

export const EcgBatchSchema = z.object({
  type: z.literal("ecg_batch"),
  seq: z.number().int().nonnegative(),
  timestamp_ms: z.number().int().nonnegative(),
  sample_rate: z.literal(ECG_SAMPLE_RATE_HZ),
  samples: z.array(z.number().finite()).min(1).max(ECG_SAMPLE_RATE_HZ * SNAPSHOT_SECONDS),
  r_peaks: z.array(z.number().int().nonnegative()).max(64).default([]),
  hr_bpm: z.number().finite().min(20).max(240).nullable().optional(),
  sqi: z.number().finite().min(0).max(1).nullable().optional(),
  lead_off: z.boolean().default(false)
});

export const StatusUpdateSchema = z.object({
  type: z.literal("status"),
  seq: z.number().int().nonnegative().optional(),
  timestamp_ms: z.number().int().nonnegative().optional(),
  hr_bpm: z.number().finite().min(20).max(240).nullable().optional(),
  sqi: z.number().finite().min(0).max(1).nullable().optional(),
  lead_off: z.boolean().optional(),
  packet_loss: z.number().int().nonnegative().optional(),
  crc_errors: z.number().int().nonnegative().optional(),
  note: z.string().max(256).optional()
});

export const MotionLevelSchema = z.enum(["still", "light", "moderate", "vigorous"]);

export const UwbReadingSchema = z.object({
  distance_m: z.number().finite().nonnegative().nullable(),
  quality: z.number().finite().min(0).max(1).nullable(),
  source: z.enum(["live", "demo", "unavailable"])
});

export const TelemetryUpdateSchema = z.object({
  type: z.literal("telemetry"),
  timestamp_ms: z.number().int().nonnegative(),
  heart: z.object({
    bpm: z.number().finite().min(20).max(240).nullable(),
    sqi: z.number().finite().min(0).max(1).nullable(),
    lead_off: z.boolean()
  }),
  imu: z.object({
    online: z.boolean().nullable(),
    roll_deg: z.number().finite().nullable(),
    pitch_deg: z.number().finite().nullable(),
    yaw_deg: z.number().finite().nullable(),
    motion_score: z.number().finite().min(0).max(100).nullable(),
    still_ratio_percent: z.number().finite().min(0).max(100).nullable(),
    level: MotionLevelSchema.nullable()
  }),
  uwb: UwbReadingSchema,
  device: z.object({
    transport: z.string().min(1).max(32),
    ecg_sample_rate_hz: z.literal(ECG_SAMPLE_RATE_HZ),
    imu_sample_rate_hz: z.number().int().positive().max(1000),
    packet_loss: z.number().int().nonnegative(),
    crc_errors: z.number().int().nonnegative(),
    data_age_ms: z.number().int().nonnegative().nullable()
  })
});

export const HealthEventUpdateSchema = z.object({
  type: z.literal("health_event"),
  event_id: z.string().min(1).max(128),
  event_revision: z.number().int().positive(),
  event_type: z.string().regex(/^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$/).max(128),
  transition: z.enum(["opened", "resolved"]),
  severity: z.enum(["info", "warning", "critical"]),
  priority: z.enum(["normal", "urgent"]),
  timestamp_ms: z.number().int().nonnegative(),
  summary: z.string().min(1).max(512)
});

export const IngestDataMessageSchema = z.discriminatedUnion("type", [
  EcgBatchSchema,
  StatusUpdateSchema,
  TelemetryUpdateSchema,
  HealthEventUpdateSchema
]);

export const LiveEcgBatchSchema = EcgBatchSchema.extend({
  session_id: SessionIdSchema,
  snapshot: z.literal(false).default(false)
});

export const LiveTelemetrySchema = TelemetryUpdateSchema.extend({
  session_id: SessionIdSchema
});

export const LiveHealthEventSchema = HealthEventUpdateSchema.extend({
  session_id: SessionIdSchema
});

export const SessionStateSchema = z.enum(["live", "stale", "offline", "signal_lost", "stopped"]);

export const PublicStatusSchema = z.object({
  type: z.literal("status"),
  session_id: SessionIdSchema,
  state: SessionStateSchema,
  timestamp_ms: z.number().int().nonnegative(),
  last_ingest_at_ms: z.number().int().nonnegative().nullable(),
  hr_bpm: z.number().finite().min(20).max(240).nullable(),
  sqi: z.number().finite().min(0).max(1).nullable(),
  lead_off: z.boolean(),
  packet_loss: z.number().int().nonnegative(),
  crc_errors: z.number().int().nonnegative(),
  note: z.string().max(256).nullable()
});

export const SnapshotSchema = z.object({
  type: z.literal("snapshot"),
  snapshot: z.literal(true),
  session_id: SessionIdSchema,
  generated_at_ms: z.number().int().nonnegative(),
  sample_rate: z.literal(ECG_SAMPLE_RATE_HZ),
  batches: z.array(LiveEcgBatchSchema.extend({ snapshot: z.literal(true) })),
  status: PublicStatusSchema.nullable(),
  telemetry: LiveTelemetrySchema.nullable().default(null),
  recent_events: z.array(LiveHealthEventSchema).max(20).default([])
});

export const ServerEventSchema = z.discriminatedUnion("type", [
  AuthOkSchema,
  IngestAckSchema,
  LiveEcgBatchSchema,
  LiveTelemetrySchema,
  LiveHealthEventSchema,
  PublicStatusSchema,
  SnapshotSchema,
  z.object({
    type: z.literal("signal_lost"),
    session_id: SessionIdSchema,
    timestamp_ms: z.number().int().nonnegative(),
    lead_off: z.literal(true)
  }),
  z.object({
    type: z.literal("session_stopped"),
    session_id: SessionIdSchema,
    timestamp_ms: z.number().int().nonnegative()
  }),
  z.object({
    type: z.literal("error"),
    code: z.string(),
    message: z.string()
  })
]);

export type IngestAuth = z.infer<typeof IngestAuthSchema>;
export type ViewerAuth = z.infer<typeof ViewerAuthSchema>;
export type AuthMessage = z.infer<typeof AuthMessageSchema>;
export type AuthOk = z.infer<typeof AuthOkSchema>;
export type IngestAck = z.infer<typeof IngestAckSchema>;
export type EcgBatch = z.infer<typeof EcgBatchSchema>;
export type StatusUpdate = z.infer<typeof StatusUpdateSchema>;
export type MotionLevel = z.infer<typeof MotionLevelSchema>;
export type UwbReading = z.infer<typeof UwbReadingSchema>;
export type TelemetryUpdate = z.infer<typeof TelemetryUpdateSchema>;
export type HealthEventUpdate = z.infer<typeof HealthEventUpdateSchema>;
export type IngestDataMessage = z.infer<typeof IngestDataMessageSchema>;
export type LiveEcgBatch = z.infer<typeof LiveEcgBatchSchema>;
export type LiveTelemetry = z.infer<typeof LiveTelemetrySchema>;
export type LiveHealthEvent = z.infer<typeof LiveHealthEventSchema>;
export type PublicStatus = z.infer<typeof PublicStatusSchema>;
export type Snapshot = z.infer<typeof SnapshotSchema>;
export type ServerEvent = z.infer<typeof ServerEventSchema>;
export type SessionState = z.infer<typeof SessionStateSchema>;

export function parseJsonMessage(data: unknown): unknown {
  if (typeof data === "string") {
    return JSON.parse(data);
  }
  if (data instanceof Buffer) {
    return JSON.parse(data.toString("utf8"));
  }
  if (data instanceof ArrayBuffer) {
    return JSON.parse(Buffer.from(data).toString("utf8"));
  }
  if (Array.isArray(data)) {
    return JSON.parse(Buffer.concat(data).toString("utf8"));
  }
  throw new Error("unsupported websocket message payload");
}

export function serializedSizeBytes(value: unknown): number {
  return Buffer.byteLength(JSON.stringify(value), "utf8");
}
