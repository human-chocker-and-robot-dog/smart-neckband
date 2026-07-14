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

export const IngestDataMessageSchema = z.discriminatedUnion("type", [EcgBatchSchema, StatusUpdateSchema]);

export const LiveEcgBatchSchema = EcgBatchSchema.extend({
  session_id: SessionIdSchema,
  snapshot: z.literal(false).default(false)
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
  status: PublicStatusSchema.nullable()
});

export const ServerEventSchema = z.discriminatedUnion("type", [
  LiveEcgBatchSchema,
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
export type EcgBatch = z.infer<typeof EcgBatchSchema>;
export type StatusUpdate = z.infer<typeof StatusUpdateSchema>;
export type IngestDataMessage = z.infer<typeof IngestDataMessageSchema>;
export type LiveEcgBatch = z.infer<typeof LiveEcgBatchSchema>;
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
